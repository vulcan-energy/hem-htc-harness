# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
# CLI for HEM HTC Harness workflows.

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path

import click

from . import __version__
from .api import HtcRunOptions, run_htc_analysis
from .build_tools import (
    build_patched_fhs_wrapper,
    build_patched_rust_hem,
    resolve_build_layout,
)
from .build_tools import (
    patched_manifest_path_for_bin as _patched_manifest_path_for_bin,
)
from .calibration import run_initial_calibration, run_iterative_calibration
from .config import (
    HarnessConfig,
    default_config_path,
    load_config,
    save_config,
)
from .empirical import (
    analyze_offsets,
    extract_mask_from_summary,
    masked_monthly_htc_from_summary,
)
from .errors import HarnessError, HarnessParameterError, HarnessUsageError
from .paths import (
    DEFAULT_OFFSETS_K,
    default_run_output_root,
    parse_window,
    resolve_and_validate_inputs,
)
from .plotting import (
    PlotSettings,
    find_summary_jsons,
    load_summary_series,
    plot_settings_from_series,
    write_plotly_html_report,
    write_plotly_report_and_open,
)
from .provenance import attach_engine_provenance_to_payload
from .reader import (
    apply_fabric_scalar_to_model,
    apply_fixed_instant_electric_heating_to_model,
)
from .workflow import (
    read_fee_metric_from_results,
    resolve_engine_env_overrides,
    run_offsets_batch,
)

# Behavioural defaults belong to the library, not to `click`. Both `run` and
# `calibrate` read them from here, so the two commands agree with each other and
# with non-CLI callers such as the browser demo.
_DEFAULTS = {f.name: f.default for f in fields(HtcRunOptions)}


def _warn_if_not_repo_venv() -> None:
    # Fail fast if h3 is not running from the repo venv.
    # When installed non-editable (e.g. CI), __file__ lives in site-packages
    # and parents[2] is NOT the repo root.  Skip the check in that case.
    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / "pyproject.toml").is_file():
        return  # not a repo checkout — nothing to enforce
    venv_dir = repo_root / ".venv"
    if not venv_dir.is_dir():
        return  # no .venv in this checkout (e.g. CI) — nothing to enforce
    expected_prefix = venv_dir.resolve()
    venv_env = os.environ.get("VIRTUAL_ENV")
    venv_prefix = None
    if venv_env:
        try:
            venv_prefix = Path(venv_env).resolve()
        except Exception:
            venv_prefix = None
    if venv_prefix and venv_prefix == expected_prefix:
        return
    try:
        exe_path = Path(sys.executable).resolve()
    except Exception:
        exe_path = None
    if exe_path and expected_prefix in exe_path.parents:
        return
    # Also allow direct invocation of the venv Python without "activating" it.
    try:
        sys_prefix = Path(sys.prefix).resolve()
    except Exception:
        sys_prefix = None
    if sys_prefix and sys_prefix == expected_prefix:
        return
    raise click.ClickException(
        "h3 must run from this repo's .venv. Activate it and retry, e.g. "
        "`source .venv/bin/activate`."
    )


# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------

_MIN_PYTHON = (3, 12)


@dataclass
class _ToolStatus:
    """Status of a single prerequisite tool."""

    name: str
    found: bool
    path: str | None = None
    version: str | None = None
    hint: str | None = None  # install suggestion


@dataclass
class _PrereqReport:
    """Aggregate prerequisite status."""

    python: _ToolStatus
    git: _ToolStatus
    cargo: _ToolStatus
    rustc: _ToolStatus

    @property
    def all_ok(self) -> bool:
        return all(t.found for t in [self.python, self.git, self.cargo, self.rustc])

    @property
    def missing(self) -> list[_ToolStatus]:
        return [
            t for t in [self.python, self.git, self.cargo, self.rustc] if not t.found
        ]


def _tool_version(cmd: str, flag: str = "--version") -> tuple[str | None, str | None]:
    """Return (path, version_string) for *cmd*, or (None, None) if not found."""
    path = shutil.which(cmd)
    if not path:
        return None, None
    try:
        result = subprocess.run(
            [path, flag], capture_output=True, text=True, timeout=10
        )
        version = (result.stdout.strip() or result.stderr.strip()).split("\n")[0]
    except Exception:
        version = "unknown"
    return path, version


def _find_python312() -> tuple[str | None, str | None]:
    """Find a Python >= 3.12 interpreter, returning (path, version_string).

    Search order:
    1. python3.12, python3.13, ... (explicit minor-version names)
    2. python3  (if version >= 3.12)
    3. python   (if version >= 3.12)
    """
    # Try explicit minor versions first (most reliable).
    for minor in range(12, 20):
        candidate = f"python3.{minor}"
        path = shutil.which(candidate)
        if path:
            try:
                result = subprocess.run(
                    [path, "--version"], capture_output=True, text=True, timeout=10
                )
                ver = result.stdout.strip() or result.stderr.strip()
                return path, ver.split("\n")[0]
            except Exception:
                continue

    # Fall back to generic names.
    for candidate in ("python3", "python"):
        path = shutil.which(candidate)
        if not path:
            continue
        try:
            result = subprocess.run(
                [path, "-c", "import sys; print(sys.version_info[:2])"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            version_tuple = eval(result.stdout.strip())  # noqa: S307
            if version_tuple >= _MIN_PYTHON:
                ver_result = subprocess.run(
                    [path, "--version"], capture_output=True, text=True, timeout=10
                )
                return path, (ver_result.stdout.strip() or ver_result.stderr.strip())
        except Exception:
            continue
    return None, None


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _has_brew() -> bool:
    return shutil.which("brew") is not None


def _check_prerequisites() -> _PrereqReport:
    """Check that Python 3.12+, git, cargo, and rustc are available."""
    # Python 3.12+
    py_path, py_ver = _find_python312()
    py_hint: str | None = None
    if not py_path:
        if _is_macos() and _has_brew():
            py_hint = "brew install python@3.12"
        elif _is_macos():
            py_hint = (
                "Install Homebrew (https://brew.sh) then: brew install python@3.12"
            )
        else:
            py_hint = "Install Python 3.12+ from https://www.python.org/downloads/"
    python_status = _ToolStatus(
        name="Python 3.12+",
        found=py_path is not None,
        path=py_path,
        version=py_ver,
        hint=py_hint,
    )

    # Git
    git_path, git_ver = _tool_version("git")
    git_hint: str | None = None
    if not git_path:
        if _is_macos():
            git_hint = "xcode-select --install"
        else:
            git_hint = "Install git via your package manager"
    git_status = _ToolStatus(
        name="git",
        found=git_path is not None,
        path=git_path,
        version=git_ver,
        hint=git_hint,
    )

    # Cargo
    cargo_path, cargo_ver = _tool_version("cargo")
    cargo_hint: str | None = None
    if not cargo_path:
        cargo_hint = "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    cargo_status = _ToolStatus(
        name="cargo",
        found=cargo_path is not None,
        path=cargo_path,
        version=cargo_ver,
        hint=cargo_hint,
    )

    # Rustc
    rustc_path, rustc_ver = _tool_version("rustc")
    rustc_hint: str | None = None
    if not rustc_path:
        rustc_hint = cargo_hint  # same install
    rustc_status = _ToolStatus(
        name="rustc",
        found=rustc_path is not None,
        path=rustc_path,
        version=rustc_ver,
        hint=rustc_hint,
    )

    return _PrereqReport(
        python=python_status,
        git=git_status,
        cargo=cargo_status,
        rustc=rustc_status,
    )


def _print_prereq_report(report: _PrereqReport) -> None:
    """Pretty-print the prerequisite check results."""
    for tool in [report.python, report.git, report.cargo, report.rustc]:
        if tool.found:
            click.secho(f"  {tool.name}: {tool.version} ({tool.path})", fg="green")
        else:
            click.secho(f"  {tool.name}: NOT FOUND", fg="red")


def _offer_install(missing: list[_ToolStatus]) -> bool:
    """Offer to install missing prerequisites. Returns True if install ran."""
    # Classify each missing tool as auto-installable or manual-only.
    auto_brew: list[str] = []  # brew formula names
    auto_rustup: str | None = None  # rustup command (at most one)
    auto_xcode: str | None = None  # xcode-select command
    manual: list[tuple[str, str]] = []  # (name, instruction)

    has_brew = _has_brew()
    for tool in missing:
        if not tool.hint:
            manual.append((tool.name, "No install suggestion available."))
            continue
        if tool.hint.startswith("brew ") and has_brew:
            auto_brew.append(tool.hint.split("brew install ")[-1])
        elif "rustup" in tool.hint:
            if auto_rustup is None:
                auto_rustup = tool.hint
        elif tool.hint.startswith("xcode-select"):
            auto_xcode = tool.hint
        else:
            # URL or generic instruction — can't auto-install.
            manual.append((tool.name, tool.hint))

    has_auto = bool(auto_brew or auto_rustup or auto_xcode)

    # Show what we can auto-install.
    if has_auto:
        click.echo("\nh3 can install the following automatically:")
        if auto_brew:
            click.echo(f"  brew install {' '.join(auto_brew)}")
        if auto_rustup:
            click.echo(f"  Rust (via rustup): {auto_rustup}")
        if auto_xcode:
            click.echo(f"  Xcode CLI tools: {auto_xcode}")

    # Show what needs manual action (always, regardless of auto).
    if manual:
        click.echo("\nThe following must be installed manually:")
        for name, hint in manual:
            click.echo(f"  {name}: {hint}")

    if not has_auto:
        click.echo("\nInstall the tools above, then re-run h3 setup.")
        return False

    if not click.confirm("\nInstall the auto-installable tools now?", default=True):
        return False

    # --- Execute auto-installs ---

    if auto_brew:
        click.echo(f"\nRunning: brew install {' '.join(auto_brew)}")
        subprocess.run(["brew", "install", *auto_brew])

    if auto_rustup:
        click.echo("\nInstalling Rust via rustup...")
        result = subprocess.run(
            ["sh", "-c", auto_rustup + " -y"],
        )
        if result.returncode == 0:
            # Add cargo/bin to PATH for the rest of this session.
            cargo_bin = Path.home() / ".cargo" / "bin"
            if str(cargo_bin) not in os.environ.get("PATH", ""):
                os.environ["PATH"] = f"{cargo_bin}:{os.environ.get('PATH', '')}"
        else:
            click.secho("rustup install failed.", fg="red")

    if auto_xcode:
        click.echo(f"\nRunning: {auto_xcode}")
        result = subprocess.run(auto_xcode.split())
        if result.returncode != 0:
            click.secho(
                "Xcode CLI tools install may need manual intervention.",
                fg="yellow",
            )

    return True


def _maybe_write_report(summary_path: Path, report_path: Path | None = None) -> None:
    """Write a Plotly HTML report for a summary JSON, warning on failure."""
    try:
        series = load_summary_series(summary_path)
        settings = plot_settings_from_series(series)
        out_path = report_path or (summary_path.parent / "htc_report.html")
        write_plotly_html_report([series], out_path, settings)
        click.echo(str(out_path))
    except Exception as exc:
        click.secho(f"Report skipped: {exc}", fg="yellow")


class ClickProgressReporter:
    """Prints batch progress and runs a spinner during each HEM run.

    All user-visible output for a batch lives here, so `workflow` stays silent
    and reusable by non-CLI callers such as the browser demo.
    """

    def __init__(self) -> None:
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None

    def note(self, message: str) -> None:
        click.secho(message, fg="yellow")

    def batch_start(self, label: str, total: int) -> None:
        click.echo(f"Running offsets for {label}")

    def run_start(self, label: str, index: int, total: int, offset_k: float) -> None:
        click.secho(
            f"Starting {label} ({index}/{total}, offset {offset_k:+.3f}K)...",
            fg="cyan",
        )
        self._start_spinner(f"Running {label} ({index}/{total})")

    def run_complete(
        self, label: str, index: int, total: int, elapsed_s: float
    ) -> None:
        self._stop_spinner()
        click.secho(f"Completed {label} in {elapsed_s:.1f}s.", fg="green")

    def _start_spinner(self, label: str) -> None:
        stop_event = threading.Event()

        def spinner() -> None:
            frames = ["|", "/", "-", "\\"]
            idx = 0
            while not stop_event.is_set():
                click.echo(f"\r{label} {frames[idx % len(frames)]}", nl=False)
                idx += 1
                time.sleep(0.15)
            click.echo("\r", nl=False)

        thread = threading.Thread(target=spinner, daemon=True)
        thread.start()
        self._stop_event = stop_event
        self._thread = thread

    def _stop_spinner(self) -> None:
        if self._stop_event is None or self._thread is None:
            return
        self._stop_event.set()
        self._thread.join()
        self._stop_event = None
        self._thread = None


class HarnessGroup(click.Group):
    """Translates library errors into their `click` equivalents.

    The library raises `HarnessError` and friends so it can run without `click`
    (notably under Pyodide). Translating here preserves the exact exit codes and
    message formatting CLI users had before the library was extracted:
    `UsageError` exits 2, `ClickException` exits 1.
    """

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except HarnessParameterError as exc:
            raise click.BadParameter(str(exc)) from exc
        except HarnessUsageError as exc:
            raise click.UsageError(str(exc)) from exc
        except HarnessError as exc:
            raise click.ClickException(str(exc)) from exc


@click.group(cls=HarnessGroup, invoke_without_command=True, help="HTC harness CLI.")
@click.version_option(
    version=__version__,
    prog_name="h3",
    message="%(prog)s, version %(version)s\nPart of Vulcan Community",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=None,
    envvar="HTC_HARNESS_CONFIG",
    help="Path to config file (defaults to ~/.config/htc-harness/config.json).",
)
@click.option("--input-dir", type=click.Path(path_type=Path), default=None)
@click.option("--weather-dir", type=click.Path(path_type=Path), default=None)
@click.option(
    "--external-conditions-dir",
    type=click.Path(path_type=Path),
    default=None,
)
@click.option("--output-dir", type=click.Path(path_type=Path), default=None)
@click.pass_context
def main(
    ctx: click.Context,
    config_path: Path | None,
    input_dir: Path | None,
    weather_dir: Path | None,
    external_conditions_dir: Path | None,
    output_dir: Path | None,
) -> None:
    # Skip the venv check for `h3 setup` — it bootstraps the venv itself.
    if ctx.invoked_subcommand != "setup":
        _warn_if_not_repo_venv()
    config = load_config(config_path)
    config = config.with_overrides(
        input_dir=input_dir,
        weather_dir=weather_dir,
        external_conditions_dir=external_conditions_dir,
        output_dir=output_dir,
    )
    ctx.obj = {
        "config": config,
        "config_path": config_path or default_config_path(),
    }
    if ctx.invoked_subcommand is None:
        click.echo("h3 — workflows\n")
        click.echo("1) Run base + offsets (simulation + analysis).")
        click.echo("   h3 run --input input/model.json --weather weather.epw\n")
        click.echo("2) Analyse an existing run (no simulation).")
        click.echo("   h3 run --dry --input input/model.json --weather weather.epw\n")
        click.echo("3) Plot outputs (interactive).")
        click.echo("   h3 plot output/\n")
        click.echo("Run `h3 --help` for full options.")


@main.command(
    "setup",
    help="One-stop setup: create venv, install deps, build HEM, and configure paths.",
)
@click.option(
    "--reconfigure",
    is_flag=True,
    default=False,
    help="Skip venv/deps/build and jump straight to path configuration.",
)
@click.pass_context
def setup_cli(ctx: click.Context, reconfigure: bool) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    venv_dir = repo_root / ".venv"
    venv_python = venv_dir / "bin" / "python"
    config_path: Path = ctx.obj["config_path"]

    # ------------------------------------------------------------------
    # Step 0: Check prerequisites (Python 3.12+, git, cargo, rustc).
    # ------------------------------------------------------------------
    click.echo("Checking prerequisites...")
    report = _check_prerequisites()
    _print_prereq_report(report)

    if not report.all_ok:
        if _offer_install(report.missing):
            # Re-check after install attempt.
            click.echo("\nRe-checking prerequisites...")
            report = _check_prerequisites()
            _print_prereq_report(report)

        if not report.all_ok:
            names = ", ".join(t.name for t in report.missing)
            raise click.ClickException(
                f"Missing prerequisites: {names}.\n"
                "Install them manually and re-run h3 setup."
            )
    click.echo()

    if not reconfigure:
        # --------------------------------------------------------------
        # Step 1: Create venv with Python 3.12+ if needed.
        # --------------------------------------------------------------
        if not venv_dir.exists() or not venv_python.exists():
            python_path = report.python.path
            if not python_path:
                raise click.ClickException(
                    "No Python 3.12+ found. Install it and re-run h3 setup."
                )
            if venv_dir.exists():
                # Broken venv (dir exists but python symlink is dead).
                click.secho(
                    ".venv exists but Python is missing — recreating...",
                    fg="yellow",
                )
                shutil.rmtree(venv_dir)
            click.echo(f"Creating .venv with {python_path}...")
            subprocess.run([python_path, "-m", "venv", str(venv_dir)], check=True)
        else:
            # Verify the existing venv uses Python 3.12+.
            try:
                result = subprocess.run(
                    [str(venv_python), "-c", "import sys; print(sys.version_info[:2])"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                existing_ver = eval(result.stdout.strip())  # noqa: S307
            except Exception:
                existing_ver = (0, 0)

            if existing_ver < _MIN_PYTHON:
                click.secho(
                    f".venv has Python {existing_ver[0]}.{existing_ver[1]} "
                    f"(need 3.12+) — recreating...",
                    fg="yellow",
                )
                shutil.rmtree(venv_dir)
                python_path = report.python.path
                if not python_path:
                    raise click.ClickException(
                        "No Python 3.12+ found. Install it and re-run h3 setup."
                    )
                subprocess.run([python_path, "-m", "venv", str(venv_dir)], check=True)
            else:
                click.secho(
                    f".venv already exists (Python {existing_ver[0]}.{existing_ver[1]}), "
                    "skipping.",
                    fg="yellow",
                )

        if not venv_python.exists():
            raise click.ClickException(
                f"Missing venv python: {venv_python}\n"
                "Try deleting .venv and re-running h3 setup."
            )

        # --------------------------------------------------------------
        # Step 2: Install dependencies (fast no-op if already up to date).
        # --------------------------------------------------------------
        click.echo("Installing editable deps (including plot)...")
        pip_env = {**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
        try:
            subprocess.run(
                [str(venv_python), "-m", "pip", "install", "-e", ".[plot]"],
                check=True,
                cwd=repo_root,
                capture_output=True,
                text=True,
                env=pip_env,
            )
        except subprocess.CalledProcessError as exc:
            output = (exc.stdout or "") + (exc.stderr or "")
            raise click.ClickException(output.strip() or "pip install failed") from exc

        # --------------------------------------------------------------
        # Step 3: Build patched HEM (smart no-op if already up to date).
        # --------------------------------------------------------------
        config = load_config(config_path)
        click.echo("Checking patched HEM (rust) build...")
        build_layout = resolve_build_layout()
        hem_bin = build_patched_rust_hem(
            build_layout.resource_root,
            work_root=build_layout.work_root,
        )
        build_patched_fhs_wrapper(
            build_layout.resource_root,
            work_root=build_layout.work_root,
            engine_dir=hem_bin.parent.parent.parent,
        )
        config = config.with_overrides(hem_bin=hem_bin)
    else:
        click.secho("Skipping venv/deps/build (--reconfigure).", fg="yellow")
        config = load_config(config_path)

    # ------------------------------------------------------------------
    # Step 4: Always run interactive config (existing values shown as defaults).
    # ------------------------------------------------------------------
    click.echo("\n--- Configure paths ---")
    config = _interactive_config_update(config, config_path)

    if not reconfigure:
        # --------------------------------------------------------------
        # Step 5: Run doctor to verify everything works.
        # --------------------------------------------------------------
        click.echo("\nRunning h3 doctor...")
        subprocess.run(
            [str(venv_python), "-m", "htc_harness.cli", "doctor"],
            check=True,
            cwd=repo_root,
        )

    click.secho("\nSetup complete.", fg="green")
    click.echo("Tip: If you use direnv, run `direnv allow` in the repo root.")


def _plot_options(f: Callable[..., object]) -> Callable[..., object]:
    # Shared Plotly report options (used by `h3 plot` and `h3 report`).
    f = click.option(
        "--agg-hours",
        type=float,
        multiple=True,
        default=None,
        help="Aggregation bin hours to show (repeatable; default uses summary bins).",
    )(f)
    f = click.option(
        "--valid-min-fraction",
        type=float,
        default=0.95,
        show_default=True,
        help="Optional block filter threshold (fraction of valid timesteps).",
    )(f)
    f = click.option(
        "--unmet-eps-kwh",
        type=float,
        default=0.0,
        show_default=True,
        help="Unmet-demand threshold in kWh per timestep for masking.",
    )(f)
    f = click.option(
        "--heating-eps-w",
        type=float,
        default=0.0,
        show_default=True,
        help="Heating-active threshold in W for masking (base system_total).",
    )(f)
    return f


@main.command(
    "plot",
    help="Plot one or more htc_summary*.json files (Plotly HTML). Opens in browser unless --html is set.",
)
@click.argument("paths", nargs=-1, type=click.Path(path_type=Path), required=True)
@click.option(
    "--no-recursive",
    is_flag=True,
    default=False,
    help="Do not search subdirectories when a PATH is a directory.",
)
@click.option(
    "--html",
    "html_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Write a single scrollable Plotly HTML report to this path (no GUI).",
)
@_plot_options
def plot_cli(
    paths: tuple[Path, ...],
    no_recursive: bool,
    html_path: Path | None,
    heating_eps_w: float,
    unmet_eps_kwh: float,
    valid_min_fraction: float | None,
    agg_hours: tuple[float, ...] | None,
) -> None:
    out_path = _plot_report_common(
        paths=list(paths),
        recursive=not no_recursive,
        html_path=html_path,
        heating_eps_w=heating_eps_w,
        unmet_eps_kwh=unmet_eps_kwh,
        valid_min_fraction=valid_min_fraction,
        agg_hours=agg_hours,
    )
    click.echo(str(out_path))


def _plot_report_common(
    *,
    paths: list[Path],
    recursive: bool,
    html_path: Path | None,
    heating_eps_w: float,
    unmet_eps_kwh: float,
    valid_min_fraction: float | None,
    agg_hours: tuple[float, ...] | None,
) -> Path:
    files = find_summary_jsons(paths, recursive=recursive)
    if not files:
        raise click.ClickException("No htc_summary*.json files found.")
    series_list = [load_summary_series(p) for p in files]
    settings = PlotSettings(
        heating_eps_w=heating_eps_w,
        unmet_eps_kwh=unmet_eps_kwh,
        valid_min_fraction=valid_min_fraction,
        agg_hours=agg_hours if agg_hours else None,
    )
    try:
        if html_path is not None:
            write_plotly_html_report(series_list, html_path, settings)
            return html_path
        report_path = files[0].parent / "htc_report.html"
        write_plotly_report_and_open(series_list, report_path, settings)
        return report_path
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


def _run_command_capture(command: list[str], cwd: Path | None) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (result.stdout or "") + (result.stderr or "")
    return True, output


@main.command(
    "run",
    help="Happy-path run: simulate (or analyse) base + offsets and write htc_summary.json.",
)
@click.option("--input", "input_path", type=click.Path(path_type=Path), required=True)
@click.option(
    "--weather", "weather_path", type=click.Path(path_type=Path), required=False
)
@click.option(
    "--external-conditions",
    "external_conditions_path",
    type=click.Path(path_type=Path),
    required=False,
    help="ExternalConditions JSON file (wrapper with ExternalConditions key, or direct object). Mutually exclusive with --weather.",
)
@click.option(
    "--out-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Optional output base directory. Defaults to <output_root>/<input_stem>.",
)
@click.option(
    "--label",
    type=str,
    default="run",
    show_default=True,
    help="Run folder prefix under the output directory.",
)
@click.option(
    "--offset",
    "offsets",
    type=float,
    multiple=True,
    required=False,
    help="Temperature offsets in K (repeatable). Defaults to -1.0, +1.0.",
)
@click.option("--hem-bin", type=click.Path(path_type=Path), default=None)
@click.option("--detailed-output-heating-cooling", is_flag=True, default=False)
@click.option(
    "--fhs-assumptions/--no-fhs-assumptions",
    default=_DEFAULTS["fhs_assumptions"],
    show_default=True,
    help="Run via the patched Future Homes Standard wrapper binary (--mode actual) instead of the core engine.",
)
@click.option(
    "--disable-fhs-heating-schedule/--no-disable-fhs-heating-schedule",
    default=_DEFAULTS["disable_fhs_heating_schedule"],
    show_default=True,
    help="Hold heating at setpoint rather than following the FHS schedule, whose setbacks make the HTC estimate noisier.",
)
@click.option(
    "--fhs-schedule-disable-via",
    type=click.Choice(["env", "preprocess"]),
    default=_DEFAULTS["fhs_schedule_disable_via"],
    show_default=True,
    help=(
        "How to disable it. 'env' sets HEM_FHS_DISABLE_HEATING_SCHEDULE, which "
        "needs the patched wrapper. 'preprocess' asks the wrapper for its "
        "preprocessed model, flattens the heating controls and runs the core "
        "engine on the result, which needs no patch and no environment "
        "variable."
    ),
)
@click.option(
    "--dry",
    "dry_mode",
    is_flag=True,
    default=False,
    help="Analyse only. Do not run simulations; expects outputs already exist under the default folder layout.",
)
@click.option(
    "--timestep-hours",
    type=float,
    default=_DEFAULTS["timestep_hours"],
    show_default=True,
)
@click.option("--window", type=str, default=None, help="start_idx,end_idx")
@click.option(
    "--heating-only/--no-heating-only",
    default=False,
    show_default=True,
    help="Limit analysis to timesteps with space-heating demand.",
)
@click.option(
    "--measured-htc",
    type=click.Path(path_type=Path),
    default=None,
    help="Optional measured HTC JSON file (see docs/methodology.md).",
)
@click.option("--extra-flag", multiple=True)
@click.option(
    "--flatten-solar",
    is_flag=True,
    default=False,
    help="Replace hourly solar irradiance with annual mean (normalise solar).",
)
@click.option(
    "--remove-solar",
    is_flag=True,
    default=False,
    help="Set all solar irradiance to zero.",
)
@click.option(
    "--flatten-wind",
    is_flag=True,
    default=False,
    help="Replace hourly wind speed with annual mean (normalise wind).",
)
@click.option(
    "--remove-wind",
    is_flag=True,
    default=False,
    help="Set all wind speeds to zero.",
)
@click.option(
    "--include-fihc/--no-fihc",
    default=_DEFAULTS["include_fihc"],
    show_default=True,
    help=(
        "Include FabricIntHeatCool (heating system radiative fraction) in "
        "q_loss and FabricHeatLoss. Accounts for radiative emitter output "
        "that bypasses the air node. Disable (--no-fihc) to replicate "
        "co-heating test methodology with 100% convective heating."
    ),
)
@click.option(
    "--fixed-electric-heating/--no-fixed-electric-heating",
    default=_DEFAULTS["fixed_electric_heating"],
    show_default=True,
    help=(
        "Replace all zone space-heating systems with fixed-size "
        "InstantElecHeater systems."
    ),
)
@click.option(
    "--fixed-electric-heater-kw-per-zone",
    type=float,
    default=_DEFAULTS["fixed_electric_heater_kw_per_zone"],
    show_default=True,
    help=(
        "Rated power (kW) per zone for fixed electric heating replacement. "
        "Used only when --fixed-electric-heating is enabled."
    ),
)
@click.option(
    "--plot/--no-plot",
    default=True,
    show_default=True,
    help="Write htc_report.html after run.",
)
@click.option(
    "--out",
    type=click.Path(path_type=Path),
    default=None,
    help="Output summary JSON path (defaults to <out-dir>/htc_summary.json).",
)
@click.option(
    "--require-patched/--no-require-patched",
    default=_DEFAULTS["require_patched"],
    show_default=True,
    help="Refuse to run unless the rust hem binary was built by h3 build-hem.",
)
@click.pass_context
def run_cli(
    ctx: click.Context,
    input_path: Path,
    weather_path: Path | None,
    external_conditions_path: Path | None,
    out_dir: Path | None,
    label: str,
    offsets: tuple[float, ...],
    hem_bin: Path | None,
    detailed_output_heating_cooling: bool,
    fhs_assumptions: bool,
    disable_fhs_heating_schedule: bool,
    fhs_schedule_disable_via: str,
    dry_mode: bool,
    timestep_hours: float,
    window: str | None,
    heating_only: bool,
    measured_htc: Path | None,
    extra_flag: tuple[str, ...],
    flatten_solar: bool,
    remove_solar: bool,
    flatten_wind: bool,
    remove_wind: bool,
    include_fihc: bool,
    fixed_electric_heating: bool,
    fixed_electric_heater_kw_per_zone: float,
    plot: bool,
    out: Path | None,
    require_patched: bool,
) -> None:
    if flatten_solar and remove_solar:
        raise click.UsageError(
            "--flatten-solar and --remove-solar are mutually exclusive"
        )
    if flatten_wind and remove_wind:
        raise click.UsageError(
            "--flatten-wind and --remove-wind are mutually exclusive"
        )
    if fixed_electric_heating and fixed_electric_heater_kw_per_zone <= 0:
        raise click.UsageError(
            "--fixed-electric-heater-kw-per-zone must be > 0 when "
            "--fixed-electric-heating is enabled"
        )

    config: HarnessConfig = ctx.obj["config"]
    hem_bin = hem_bin or config.hem_bin
    if hem_bin:
        _check_patched_binary(hem_bin, require_patched=require_patched)

    result = run_htc_analysis(
        options=HtcRunOptions(
            input_path=input_path,
            weather_path=weather_path,
            external_conditions_path=external_conditions_path,
            out_dir=out_dir,
            summary_path=out,
            label=label,
            offsets=tuple(offsets),
            include_base=True,
            hem_bin=hem_bin,
            timestep_hours=timestep_hours,
            window=window,
            heating_only=heating_only,
            include_fihc=include_fihc,
            measured_htc_path=measured_htc,
            detailed_output_heating_cooling=detailed_output_heating_cooling,
            fhs_assumptions=fhs_assumptions,
            disable_fhs_heating_schedule=disable_fhs_heating_schedule,
            fhs_schedule_disable_via=fhs_schedule_disable_via,
            extra_flag=extra_flag,
            require_patched=require_patched,
            fixed_electric_heating=fixed_electric_heating,
            fixed_electric_heater_kw_per_zone=fixed_electric_heater_kw_per_zone,
            dry_mode=dry_mode,
            flatten_solar=flatten_solar,
            remove_solar=remove_solar,
            flatten_wind=flatten_wind,
            remove_wind=remove_wind,
        ),
        config=config,
        progress=ClickProgressReporter(),
    )
    summary_path = result.summary_path
    click.echo(str(summary_path))
    if plot:
        _maybe_write_report(summary_path)


@main.command(
    "calibrate",
    help="Run baseline + perturbations, fit scalars, and write calibrated outputs plus baseline summary artifacts.",
)
@click.option("--input", "input_path", type=click.Path(path_type=Path), required=True)
@click.option(
    "--weather", "weather_path", type=click.Path(path_type=Path), required=False
)
@click.option(
    "--external-conditions",
    "external_conditions_path",
    type=click.Path(path_type=Path),
    required=False,
    help="ExternalConditions JSON file (wrapper with ExternalConditions key, or direct object). Mutually exclusive with --weather.",
)
@click.option(
    "--out-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Optional output base directory. Defaults to <output_root>/<input_stem>.",
)
@click.option(
    "--label",
    type=str,
    default="calibration",
    show_default=True,
    help="Run folder prefix under the output directory.",
)
@click.option(
    "--offset",
    "offsets",
    type=float,
    multiple=True,
    required=False,
    help="Temperature offsets in K (repeatable). Defaults to -1.0, +1.0.",
)
@click.option("--hem-bin", type=click.Path(path_type=Path), default=None)
@click.option("--detailed-output-heating-cooling", is_flag=True, default=False)
@click.option(
    "--fhs-assumptions/--no-fhs-assumptions",
    default=_DEFAULTS["fhs_assumptions"],
    show_default=True,
    help="Run via the patched Future Homes Standard wrapper binary (--mode actual) instead of the core engine.",
)
@click.option(
    "--disable-fhs-heating-schedule/--no-disable-fhs-heating-schedule",
    default=_DEFAULTS["disable_fhs_heating_schedule"],
    show_default=True,
    help="Disable FHS heating schedule in the patched HEM (sets HEM_FHS_DISABLE_HEATING_SCHEDULE=1).",
)
@click.option(
    "--dry",
    "dry_mode",
    is_flag=True,
    default=False,
    help="Analyse only. Do not run simulations; expects outputs already exist under the default folder layout.",
)
@click.option(
    "--timestep-hours",
    type=float,
    default=_DEFAULTS["timestep_hours"],
    show_default=True,
)
@click.option("--window", type=str, default=None, help="start_idx,end_idx")
@click.option("--heating-only", is_flag=True, default=False)
@click.option(
    "--fixed-electric-heating/--no-fixed-electric-heating",
    default=_DEFAULTS["fixed_electric_heating"],
    show_default=True,
    help=(
        "Replace all zone space-heating systems with fixed-size "
        "InstantElecHeater systems."
    ),
)
@click.option(
    "--fixed-electric-heater-kw-per-zone",
    type=float,
    default=_DEFAULTS["fixed_electric_heater_kw_per_zone"],
    show_default=True,
    help=(
        "Rated power (kW) per zone for fixed electric heating replacement. "
        "Used only when --fixed-electric-heating is enabled."
    ),
)
@click.option(
    "--extra-flag",
    multiple=True,
    help="Additional raw flags forwarded to the HEM engine.",
)
@click.option(
    "--fee-metrics/--no-fee-metrics",
    default=True,
    show_default=True,
    help=(
        "Run lightweight FEE postprocessing for baseline and final calibrated "
        "models only (no intermediate iterative steps)."
    ),
)
@click.option(
    "--measured-htc",
    type=click.Path(path_type=Path),
    required=True,
    help="Measured HTC JSON file (see docs/methodology.md).",
)
@click.option("--delta-scalar", type=float, default=0.2, show_default=True)
@click.option("--lambda-reg", type=float, default=0.01, show_default=True)
@click.option("--scalar-min", type=float, default=0.1, show_default=True)
@click.option("--scalar-max", type=float, default=2.5, show_default=True)
@click.option("--heating-eps-w", type=float, default=0.0, show_default=True)
@click.option("--unmet-eps-kwh", type=float, default=0.0, show_default=True)
@click.option("--valid-min-fraction", type=float, default=0.95, show_default=True)
@click.option(
    "--calibration-stat",
    type=click.Choice(["mean", "median"], case_sensitive=False),
    default="mean",
    show_default=True,
    help="Aggregation statistic for masked monthly calibration HTC.",
)
@click.option(
    "--mask-block-hours",
    type=float,
    default=24.0,
    show_default=True,
    help="Block-level gating granularity in hours. Blocks with fewer than valid-min-fraction passing timesteps are dropped entirely. Set to 0 to disable.",
)
@click.option(
    "--out",
    type=click.Path(path_type=Path),
    default=None,
    help="Output summary JSON path (defaults to <out-dir>/htc_summary.json).",
)
@click.option(
    "--opt-steps",
    type=click.IntRange(0, None),
    default=5,
    show_default=True,
    help="Number of iterative calibration refinement steps after the initial solve.",
)
@click.option(
    "--opt-tol-w-per-k",
    type=float,
    default=0.5,
    show_default=True,
    help="Stop iterative refinement early when |residual| <= this tolerance.",
)
@click.option(
    "--require-patched/--no-require-patched",
    default=_DEFAULTS["require_patched"],
    show_default=True,
    help="Refuse to run unless the rust hem binary was built by h3 build-hem.",
)
@click.option(
    "--plot/--no-plot",
    default=True,
    show_default=True,
    help="Write htc_report.html plus htc_report_BASELINE.html after calibration.",
)
@click.pass_context
def calibrate_cli(
    ctx: click.Context,
    input_path: Path,
    weather_path: Path | None,
    external_conditions_path: Path | None,
    out_dir: Path | None,
    label: str,
    offsets: tuple[float, ...],
    hem_bin: Path | None,
    detailed_output_heating_cooling: bool,
    fhs_assumptions: bool,
    disable_fhs_heating_schedule: bool,
    dry_mode: bool,
    timestep_hours: float,
    window: str | None,
    heating_only: bool,
    fixed_electric_heating: bool,
    fixed_electric_heater_kw_per_zone: float,
    extra_flag: tuple[str, ...],
    fee_metrics: bool,
    measured_htc: Path,
    delta_scalar: float,
    lambda_reg: float,
    scalar_min: float,
    scalar_max: float,
    heating_eps_w: float,
    unmet_eps_kwh: float,
    valid_min_fraction: float,
    calibration_stat: str,
    mask_block_hours: float,
    out: Path | None,
    opt_steps: int,
    opt_tol_w_per_k: float,
    require_patched: bool,
    plot: bool,
) -> None:
    # Calibration run: baseline + perturbations + calibrated run (rust only).
    if fixed_electric_heating and fixed_electric_heater_kw_per_zone <= 0:
        raise click.UsageError(
            "--fixed-electric-heater-kw-per-zone must be > 0 when "
            "--fixed-electric-heating is enabled"
        )

    config: HarnessConfig = ctx.obj["config"]
    hem_bin = hem_bin or config.hem_bin
    if hem_bin is None:
        raise click.UsageError("hem_bin is required for rust calibration")
    _check_patched_binary(hem_bin, require_patched=require_patched)

    resolved_input, resolved_weather, resolved_external_conditions = (
        resolve_and_validate_inputs(
            config=config,
            input_path=input_path,
            weather_path=weather_path,
            external_conditions_path=external_conditions_path,
            dry_mode=dry_mode,
        )
    )

    output_root = default_run_output_root(config, resolved_input, out_dir)
    offsets_list = list(offsets) if offsets else list(DEFAULT_OFFSETS_K)
    if 0.0 not in offsets_list:
        offsets_list.insert(0, 0.0)

    start_idx, end_idx = parse_window(window)
    summary_path = out or (output_root / "htc_summary.json")
    baseline_summary_path = summary_path.with_name(
        f"{summary_path.stem}_baseline{summary_path.suffix or '.json'}"
    )
    calibration_stat = calibration_stat.lower()
    mask_block_hours_eff = (
        mask_block_hours if mask_block_hours and mask_block_hours > 0 else None
    )
    scalar_bounds = (scalar_min, scalar_max)
    if scalar_bounds[0] > scalar_bounds[1]:
        raise click.UsageError("--scalar-min cannot be greater than --scalar-max")

    fixed_heating_info: dict[str, object] | None = None
    base_model_transform: Callable[[dict[str, object]], dict[str, object]] | None = None
    if fixed_electric_heating:

        def _fixed_electric_transform(model: dict[str, object]) -> dict[str, object]:
            nonlocal fixed_heating_info
            transformed, info = apply_fixed_instant_electric_heating_to_model(
                model,
                rated_power_kw_per_zone=fixed_electric_heater_kw_per_zone,
            )
            if fixed_heating_info is None:
                fixed_heating_info = info
            return transformed

        base_model_transform = _fixed_electric_transform

    def _run_step(
        batch_label: str,
        *,
        env_overrides: dict[str, str] | None = None,
        model_transform=None,
        mask_override: list[bool] | None = None,
    ) -> tuple[dict, dict]:
        """Run offsets, analyse, and compute masked monthly summary."""
        composed_transform = model_transform
        if base_model_transform is not None and model_transform is not None:

            def _composed_transform(model: dict[str, object]) -> dict[str, object]:
                return model_transform(base_model_transform(model))

            composed_transform = _composed_transform
        elif base_model_transform is not None:
            composed_transform = base_model_transform

        dirs = run_offsets_batch(
            dry_mode=dry_mode,
            output_root=output_root,
            batch_label=batch_label,
            resolved_input=resolved_input,
            resolved_weather=resolved_weather,
            resolved_external_conditions=resolved_external_conditions,
            offsets_list=offsets_list,
            hem_bin=hem_bin,
            detailed_output_heating_cooling=detailed_output_heating_cooling,
            fhs_assumptions=fhs_assumptions,
            extra_flag=extra_flag,
            disable_fhs_heating_schedule=disable_fhs_heating_schedule,
            env_overrides=env_overrides,
            model_transform=composed_transform,
            progress=ClickProgressReporter(),
        )
        payload = analyze_offsets(
            results_dirs_by_offset_k=dirs,
            timestep_hours=timestep_hours,
            start_idx=start_idx,
            end_idx=end_idx,
            heating_only=heating_only,
            measured_htc_path=measured_htc,
        )
        summary = masked_monthly_htc_from_summary(
            payload,
            heating_eps_w=heating_eps_w,
            unmet_eps_kwh=unmet_eps_kwh,
            valid_min_fraction=valid_min_fraction,
            mask_block_hours=mask_block_hours_eff,
            mask_override=mask_override,
            aggregate_stat=calibration_stat,
        )
        return payload, summary

    initial_result = run_initial_calibration(
        label=label,
        delta_scalar=delta_scalar,
        lambda_reg=lambda_reg,
        scalar_bounds=scalar_bounds,
        heating_eps_w=heating_eps_w,
        unmet_eps_kwh=unmet_eps_kwh,
        valid_min_fraction=valid_min_fraction,
        mask_block_hours_eff=mask_block_hours_eff,
        calibration_stat=calibration_stat,
        extract_mask_fn=extract_mask_from_summary,
        run_step=_run_step,
    )
    if bool(initial_result["failed_pre_solve"]):
        diagnostic_payload = initial_result.get("diagnostic_payload")
        if isinstance(diagnostic_payload, dict):
            summary_path.write_text(json.dumps(diagnostic_payload, indent=2))
            click.secho(
                "Calibration diagnostics written (solver did not run).",
                fg="yellow",
            )
            click.echo(str(summary_path))
            if plot:
                _maybe_write_report(summary_path)
        raise click.ClickException(str(initial_result["error_message"]))

    baseline_summary = initial_result["baseline_summary"]
    baseline_payload = initial_result["baseline_payload"]
    baseline_mask = initial_result["baseline_mask"]
    htc_target = float(initial_result["htc_target"])
    htc_baseline = float(initial_result["htc_baseline"])
    global_summary = initial_result["global_summary"]
    s_global = float(initial_result["s_global"])
    lambda_reg_effective = float(initial_result["lambda_reg_effective"])
    calibrated_scalar = float(initial_result["calibrated_scalar"])

    if fixed_electric_heating:
        baseline_payload.setdefault("job", {})
        if isinstance(baseline_payload["job"], dict):
            baseline_payload["job"]["fixed_electric_heating"] = (
                fixed_heating_info
                if fixed_heating_info is not None
                else {
                    "rated_power_kw_per_zone": fixed_electric_heater_kw_per_zone,
                }
            )
    attach_engine_provenance_to_payload(
        baseline_payload,
        hem_bin=hem_bin,
        require_patched=require_patched,
        env_overrides=resolve_engine_env_overrides(
            env_overrides=None,
            disable_fhs_heating_schedule=disable_fhs_heating_schedule,
        ),
        extra_flags=list(extra_flag),
    )
    baseline_summary_path.write_text(json.dumps(baseline_payload, indent=2))
    if plot:
        _maybe_write_report(
            baseline_summary_path,
            report_path=summary_path.parent / "htc_report_BASELINE.html",
        )

    def _run_calibrated_step(
        *,
        batch_suffix: str,
        mask_override: list[bool] | None,
        scalar: float,
    ) -> tuple[dict, dict, dict[str, int] | None, dict[str, str]]:
        counts: dict[str, int] | None = None

        def _calibrated_fabric_transform(model: dict[str, object]) -> dict[str, object]:
            nonlocal counts
            scaled, model_counts = apply_fabric_scalar_to_model(model, scalar)
            if counts is None:
                counts = model_counts
            return scaled

        cal_env_local = {"HEM_ACH_CAL_FACTOR": f"{scalar:.6f}"}
        payload_local, summary_local = _run_step(
            f"{label}_{batch_suffix}",
            env_overrides=cal_env_local,
            model_transform=_calibrated_fabric_transform,
            mask_override=mask_override,
        )
        return payload_local, summary_local, counts, cal_env_local

    calibrated_payload, calibrated_summary, calibrated_counts, cal_env = (
        _run_calibrated_step(
            batch_suffix="calibrated",
            mask_override=baseline_mask,
            scalar=calibrated_scalar,
        )
    )
    attach_engine_provenance_to_payload(
        calibrated_payload,
        hem_bin=hem_bin,
        require_patched=require_patched,
        env_overrides=resolve_engine_env_overrides(
            env_overrides=cal_env,
            disable_fhs_heating_schedule=disable_fhs_heating_schedule,
        ),
        extra_flags=list(extra_flag),
    )
    htc_calibrated = calibrated_summary.get("masked_aggregate_w_per_k")
    residual = (
        float(htc_calibrated) - float(htc_target)
        if isinstance(htc_calibrated, (int, float))
        else None
    )
    iterative_result = run_iterative_calibration(
        opt_steps=opt_steps,
        opt_tol_w_per_k=opt_tol_w_per_k,
        baseline_mask=baseline_mask,
        label=label,
        htc_target=float(htc_target),
        delta_scalar=delta_scalar,
        lambda_reg_effective=lambda_reg_effective,
        scalar_bounds=scalar_bounds,
        calibrated_scalar=calibrated_scalar,
        calibrated_payload=calibrated_payload,
        calibrated_summary=calibrated_summary,
        calibrated_counts=calibrated_counts,
        cal_env=cal_env,
        htc_calibrated=(
            float(htc_calibrated) if isinstance(htc_calibrated, (int, float)) else None
        ),
        residual=residual,
        run_step=_run_step,
        run_calibrated_step=_run_calibrated_step,
    )
    calibrated_scalar = float(iterative_result["calibrated_scalar"])
    calibrated_payload = iterative_result["calibrated_payload"]
    calibrated_summary = iterative_result["calibrated_summary"]
    calibrated_counts = iterative_result["calibrated_counts"]
    cal_env = iterative_result["cal_env"]
    htc_calibrated = iterative_result["htc_calibrated"]
    residual = iterative_result["residual"]
    iterative_history = iterative_result["iterative_history"]
    iterative_stopped_reason = iterative_result["iterative_stopped_reason"]
    iterative_executed_steps = int(iterative_result["iterative_executed_steps"])
    if iterative_executed_steps > 0:
        s_global = float(iterative_result["s_global_final"])

    def _summary_metric(payload: dict, key: str) -> float | None:
        summary = payload.get("summary")
        if isinstance(summary, dict):
            value = summary.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return None

    def _dynamic_total(payload: dict) -> float | None:
        derived = payload.get("derived")
        if not isinstance(derived, dict):
            return None
        comps_by_offset = derived.get("hem_htc_components_w_per_k_by_offset_k")
        if not isinstance(comps_by_offset, dict):
            return None
        base_comps = comps_by_offset.get("0.0")
        if not isinstance(base_comps, dict):
            return None
        parts: list[float] = []
        for key in ("FabricHeatLoss", "ThermalBridges", "InfiltrationVentilation"):
            values = base_comps.get(key)
            if not isinstance(values, list):
                continue
            finite = [float(v) for v in values if isinstance(v, (int, float))]
            if finite:
                parts.append(sum(finite) / len(finite))
        return sum(parts) if parts else None

    htc_static_baseline = _summary_metric(baseline_payload, "htc_design_w_per_k")
    htc_static_calibrated = _summary_metric(calibrated_payload, "htc_design_w_per_k")
    htc_dynamic_baseline = _dynamic_total(baseline_payload)
    htc_dynamic_calibrated = _dynamic_total(calibrated_payload)
    htc_empirical_baseline = _summary_metric(baseline_payload, "htc_empirical_w_per_k")
    htc_empirical_calibrated = _summary_metric(
        calibrated_payload, "htc_empirical_w_per_k"
    )

    if fixed_electric_heating:
        calibrated_payload.setdefault("job", {})
        if isinstance(calibrated_payload["job"], dict):
            calibrated_payload["job"]["fixed_electric_heating"] = (
                fixed_heating_info
                if fixed_heating_info is not None
                else {
                    "rated_power_kw_per_zone": fixed_electric_heater_kw_per_zone,
                }
            )

    fee_metrics_payload: dict[str, object] = {"enabled": bool(fee_metrics)}
    if fee_metrics:
        if dry_mode:
            fee_metrics_payload["status"] = "skipped_dry_mode"
        else:
            fee_flag = "--future-homes-standard-FEE"

            def _run_fee_probe(
                *,
                probe_suffix: str,
                env_overrides: dict[str, str] | None,
                model_transform: Callable[[dict[str, object]], dict[str, object]]
                | None,
            ) -> float | None:
                fee_dirs = run_offsets_batch(
                    dry_mode=False,
                    output_root=output_root,
                    batch_label=f"{label}_{probe_suffix}",
                    resolved_input=resolved_input,
                    resolved_weather=resolved_weather,
                    resolved_external_conditions=resolved_external_conditions,
                    offsets_list=[0.0],
                    hem_bin=hem_bin,
                    detailed_output_heating_cooling=detailed_output_heating_cooling,
                    fhs_assumptions=False,
                    extra_flag=(fee_flag,),
                    disable_fhs_heating_schedule=disable_fhs_heating_schedule,
                    env_overrides=env_overrides,
                    model_transform=model_transform,
                    progress=ClickProgressReporter(),
                )
                return read_fee_metric_from_results(fee_dirs[0.0])

            baseline_fee = _run_fee_probe(
                probe_suffix="fee_baseline",
                env_overrides=None,
                model_transform=base_model_transform,
            )

            def _calibrated_fee_transform(
                model: dict[str, object],
            ) -> dict[str, object]:
                scaled, _counts = apply_fabric_scalar_to_model(model, calibrated_scalar)
                return scaled

            composed_calibrated_fee_transform = _calibrated_fee_transform
            if base_model_transform is not None:

                def _composed_calibrated_fee_transform(
                    model: dict[str, object],
                ) -> dict[str, object]:
                    return _calibrated_fee_transform(base_model_transform(model))

                composed_calibrated_fee_transform = _composed_calibrated_fee_transform

            calibrated_fee = _run_fee_probe(
                probe_suffix="fee_calibrated",
                env_overrides={"HEM_ACH_CAL_FACTOR": f"{calibrated_scalar:.6f}"},
                model_transform=composed_calibrated_fee_transform,
            )
            fee_metrics_payload.update(
                {
                    "status": "ok",
                    "flag": fee_flag,
                    "metric_name": "Fabric Energy Efficiency (kWh/m2.yr)",
                    "baseline_kwh_per_m2_yr": baseline_fee,
                    "calibrated_kwh_per_m2_yr": calibrated_fee,
                }
            )

    calibrated_payload["calibration"] = {
        "runner": "rust",
        "inputs": {
            "delta_scalar": delta_scalar,
            "calibration_stat": calibration_stat,
            "lambda_reg": lambda_reg_effective,
            "scalar_bounds": [scalar_min, scalar_max],
            "opt_steps": opt_steps,
            "opt_tol_w_per_k": opt_tol_w_per_k,
            "extra_flags": list(extra_flag),
        },
        "masking": {
            "heating_eps_w": heating_eps_w,
            "unmet_eps_kwh": unmet_eps_kwh,
            "valid_min_fraction": valid_min_fraction,
            "mask_block_hours": mask_block_hours_eff,
        },
        "sensitivities": {
            "s_global": s_global,
        },
        "scalars": {
            "scalar": calibrated_scalar,
        },
        "metrics": {
            "htc_target_w_per_k": htc_target,
            "htc_baseline_w_per_k": htc_baseline,
            "htc_calibrated_w_per_k": htc_calibrated,
            "residual_w_per_k": residual,
            "htc_static_baseline_w_per_k": htc_static_baseline,
            "htc_static_calibrated_w_per_k": htc_static_calibrated,
            "htc_dynamic_baseline_w_per_k": htc_dynamic_baseline,
            "htc_dynamic_calibrated_w_per_k": htc_dynamic_calibrated,
            "htc_empirical_baseline_w_per_k": htc_empirical_baseline,
            "htc_empirical_calibrated_w_per_k": htc_empirical_calibrated,
        },
        "monthly": {
            "labels": baseline_summary.get("labels"),
            "baseline_htc_w_per_k": baseline_summary.get("monthly_htc_w_per_k"),
            "global_htc_w_per_k": global_summary.get("monthly_htc_w_per_k"),
            "calibrated_htc_w_per_k": calibrated_summary.get("monthly_htc_w_per_k"),
            "mask_reason": baseline_summary.get("mask_reason"),
        },
        "iterative": {
            "enabled": opt_steps > 0,
            "max_steps": opt_steps,
            "executed_steps": iterative_executed_steps,
            "tolerance_w_per_k": opt_tol_w_per_k,
            "stopped_reason": iterative_stopped_reason,
            "history": iterative_history,
        },
        "fee_metrics": fee_metrics_payload,
    }

    summary_path.write_text(json.dumps(calibrated_payload, indent=2))
    click.secho("Calibration summary", fg="cyan")
    click.echo(f"Target HTC (W/K): {htc_target:.3f}")
    click.echo(f"Baseline HTC (W/K): {float(htc_baseline):.3f}")
    if isinstance(htc_calibrated, (int, float)):
        click.echo(f"Calibrated HTC (W/K): {float(htc_calibrated):.3f}")
    if residual is not None:
        click.echo(f"Residual (W/K): {residual:.3f}")
    if fee_metrics_payload.get("status") == "ok":
        baseline_fee = fee_metrics_payload.get("baseline_kwh_per_m2_yr")
        calibrated_fee = fee_metrics_payload.get("calibrated_kwh_per_m2_yr")
        click.echo("FEE (kWh/m2.yr):")
        click.echo(f"  Baseline: {baseline_fee}")
        click.echo(f"  Calibrated: {calibrated_fee}")
    click.echo(f"Scalar: {calibrated_scalar:.3f}")
    if calibrated_counts and calibrated_counts.get("ground_u_value_capped", 0) > 0:
        click.secho(
            "Warning: ground u_value was capped to keep r_vi > 0.",
            fg="yellow",
        )
    click.echo(str(summary_path))
    if plot:
        _maybe_write_report(summary_path)


@main.command("config", help="Show or update the per-user config file.")
@click.option("--write", is_flag=True, default=False)
@click.option("--hem-bin", type=click.Path(path_type=Path), default=None)
@click.option("--input-dir", type=click.Path(path_type=Path), default=None)
@click.option("--weather-dir", type=click.Path(path_type=Path), default=None)
@click.option(
    "--external-conditions-dir",
    type=click.Path(path_type=Path),
    default=None,
)
@click.option("--output-dir", type=click.Path(path_type=Path), default=None)
@click.option("--out", type=click.Path(path_type=Path), default=None)
@click.pass_context
def config_cli(
    ctx: click.Context,
    write: bool,
    hem_bin: Path | None,
    input_dir: Path | None,
    weather_dir: Path | None,
    external_conditions_dir: Path | None,
    output_dir: Path | None,
    out: Path | None,
) -> None:
    config: HarnessConfig = ctx.obj["config"]
    config_path: Path = ctx.obj["config_path"]
    updated = config.with_overrides(
        hem_bin=hem_bin,
        input_dir=input_dir,
        weather_dir=weather_dir,
        external_conditions_dir=external_conditions_dir,
        output_dir=output_dir,
    )
    if write:
        save_config(updated, config_path)
    payload = updated.to_dict()
    payload["config_path"] = str(config_path)
    payload["config_exists"] = config_path.exists()
    output = json.dumps(payload, indent=2)
    if out:
        out.write_text(output)
        return
    click.echo(output)


@main.command("doctor", help="Check configuration and report diagnostics.")
@click.option("--out", type=click.Path(path_type=Path), default=None)
@click.pass_context
def doctor_cli(
    ctx: click.Context,
    out: Path | None,
) -> None:
    """Show the actual saved config + tool versions as JSON."""
    config: HarnessConfig = ctx.obj["config"]
    config_path: Path = ctx.obj["config_path"]

    # Prerequisite tool check.
    report = _check_prerequisites()
    prereqs: dict[str, object] = {}
    for tool in [report.python, report.git, report.cargo, report.rustc]:
        prereqs[tool.name] = {
            "found": tool.found,
            "path": tool.path,
            "version": tool.version,
        }

    checks: dict[str, object] = {
        "prerequisites": prereqs,
        "config_path": str(config_path),
        "config_exists": config_path.exists(),
        "runner": "rust",
        "hem_bin": str(config.hem_bin) if config.hem_bin else None,
        "input_dir": str(config.input_dir) if config.input_dir else None,
        "weather_dir": str(config.weather_dir) if config.weather_dir else None,
        "external_conditions_dir": (
            str(config.external_conditions_dir)
            if config.external_conditions_dir
            else None
        ),
        "output_dir": str(config.output_dir) if config.output_dir else None,
    }
    diagnostics: dict[str, object] = {}
    diagnostics["hem_bin_exists"] = bool(config.hem_bin and config.hem_bin.exists())
    diagnostics["hem_bin_executable"] = bool(
        config.hem_bin and os.access(config.hem_bin, os.X_OK)
    )
    if config.hem_bin and diagnostics["hem_bin_executable"]:
        ok, output = _run_command_capture([str(config.hem_bin), "--help"], None)
        diagnostics["hem_bin_help_ok"] = ok
        diagnostics["hem_bin_help_snippet"] = output[:200]
    checks["diagnostics"] = diagnostics
    output = json.dumps(checks, indent=2)
    if out:
        out.write_text(output)
        return
    click.echo(output)


def _interactive_config_update(
    config: HarnessConfig, config_path: Path
) -> HarnessConfig:
    """Prompt the user for directory paths.

    Offers two modes:
    - **vulcan**: derive input/weather/external-conditions from a Vulcan
      workspace root (e.g. ``rust-hem-runner``).
    - **individual**: prompt for each directory separately.

    ``hem_bin`` is set automatically by ``h3 setup`` / ``h3 build-hem`` and can
    be overridden with ``h3 config --write``.
    """

    def _prompt_dir(label: str, current: Path | None) -> Path | None:
        default = str(current) if current else ""
        value = click.prompt(label, default=default, show_default=bool(default))
        return Path(value) if value else None

    mode = click.prompt(
        "Connect to a Vulcan workspace, or set up directories individually?",
        type=click.Choice(["vulcan", "individual"], case_sensitive=False),
        default="individual",
    )

    if mode == "vulcan":
        input_dir, weather_dir, ec_dir, output_dir = _vulcan_workspace_paths(config)
    else:
        input_dir = _prompt_dir(
            "Input dir (where model JSON files live)", config.input_dir
        )
        weather_dir = _prompt_dir(
            "Weather dir (where EPW files live, or leave empty)", config.weather_dir
        )
        ec_dir = _prompt_dir(
            "External conditions dir (where EC JSON files live, or leave empty)",
            config.external_conditions_dir,
        )
        output_dir = _prompt_dir(
            "Output dir (where results are written)", config.output_dir
        )

    updated = config.with_overrides(
        input_dir=input_dir,
        weather_dir=weather_dir,
        external_conditions_dir=ec_dir,
        output_dir=output_dir,
    )
    save_config(updated, config_path)
    click.secho(f"Config saved to {config_path}", fg="green")
    return updated


def _vulcan_workspace_paths(
    config: HarnessConfig,
) -> tuple[Path | None, Path | None, Path | None, Path | None]:
    """Derive input/weather/ec/output paths from a Vulcan workspace root."""
    raw = click.prompt("Vulcan workspace root", default="", show_default=False)
    if not raw:
        raise click.ClickException("Vulcan workspace root is required.")
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise click.ClickException(f"Directory not found: {root}")

    bp = root / "input" / "batch_parameters"
    input_dir = bp / "base_json"
    weather_dir = bp / "weather_files"
    ec_dir = bp / "external_conditions"
    output_dir = root / "output"

    # Show resolved paths with existence check.
    click.echo("\nResolved paths:")
    for label, d in [
        ("Input dir", input_dir),
        ("Weather dir", weather_dir),
        ("External conds", ec_dir),
        ("Output dir", output_dir),
    ]:
        marker = (
            click.style(" ✓", fg="green")
            if d.exists()
            else click.style(" ✗ (not found)", fg="yellow")
        )
        click.echo(f"  {label:<17s} {d}{marker}")

    # Let the user override output dir if desired.
    override = click.prompt(
        "\nOutput dir (where results are written)",
        default=str(output_dir),
        show_default=True,
    )
    output_dir = Path(override) if override else output_dir

    return input_dir, weather_dir, ec_dir, output_dir


def _check_patched_binary(hem_bin: Path, require_patched: bool) -> None:
    manifest_path = _patched_manifest_path_for_bin(hem_bin)
    if manifest_path is None:
        if require_patched:
            raise click.ClickException(
                "Patched build required, but hem_bin is not under .patch-workdir.\n\n"
                "Fix:\n"
                "- Run `h3 build-hem` (or `h3 setup`) to build a patched HEM.\n"
                "- Or pass `--no-require-patched` to proceed without verification."
            )
        click.secho(
            "Warning: hem_bin is not under .patch-workdir; "
            "patched build cannot be verified.",
            fg="yellow",
        )
        return
    if not manifest_path.exists():
        if require_patched:
            raise click.ClickException(
                "Patched build required, but patched manifest not found:\n"
                f"- {manifest_path}\n\n"
                "Fix:\n"
                "- Run `h3 build-hem` (or `h3 setup`) to rebuild.\n"
                "- Or pass `--no-require-patched` to proceed without verification."
            )
        click.secho(
            f"Warning: patched manifest not found at {manifest_path}.",
            fg="yellow",
        )


@main.command(
    "build-hem",
    help="Build patched HEM from patches/manifest.json and optionally write config.",
)
@click.option(
    "--clean/--no-clean",
    default=False,
    show_default=True,
    help="If set, delete .patch-workdir before building. Default reuses an existing matching build when possible.",
)
@click.option(
    "--write-config/--no-write-config",
    default=True,
    show_default=True,
)
@click.pass_context
def build_hem_cli(
    ctx: click.Context,
    clean: bool,
    write_config: bool,
) -> None:
    config: HarnessConfig = ctx.obj["config"]
    config_path: Path = ctx.obj["config_path"]
    build_layout = resolve_build_layout()

    built = build_patched_rust_hem(
        build_layout.resource_root,
        work_root=build_layout.work_root,
        clean=clean,
    )
    click.echo(f"Binary: {built}")

    # The FHS wrapper is a separate repo and binary from engine v1.0.0-alpha,
    # built against the patched engine so both share one build.
    fhs_built = build_patched_fhs_wrapper(
        build_layout.resource_root,
        work_root=build_layout.work_root,
        engine_dir=built.parent.parent.parent,
        clean=clean,
    )
    click.echo(f"FHS binary: {fhs_built}")
    if write_config:
        updated = config.with_overrides(
            hem_bin=built,
        )
        save_config(updated, config_path)


if __name__ == "__main__":
    main()
