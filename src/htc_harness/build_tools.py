# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sysconfig
from dataclasses import dataclass
from pathlib import Path

import click


@dataclass(frozen=True)
class BuildLayout:
    """Read-only build inputs and the writable checkout/cache they drive."""

    resource_root: Path
    work_root: Path


def resolve_build_layout() -> BuildLayout:
    """Locate patch resources for a source checkout or an installed wheel.

    Source checkouts keep their existing `.patch-workdir` layout. A wheel's
    immutable resources are installed under the Python data directory while
    cloned repositories and Cargo output live in the user's cache.
    """

    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").is_file() and (
        source_root / "patches" / "manifest.json"
    ).is_file():
        return BuildLayout(resource_root=source_root, work_root=source_root)

    resource_root = Path(sysconfig.get_path("data")) / "share" / "htc-harness"
    if not (resource_root / "patches" / "manifest.json").is_file():
        raise click.ClickException(
            "Installed h3 package is missing its HEM/FHS build resources. "
            "Reinstall the wheel and retry."
        )

    cache_override = os.environ.get("HTC_HARNESS_BUILD_ROOT")
    if cache_override:
        work_root = Path(cache_override)
    else:
        cache_home = os.environ.get("XDG_CACHE_HOME")
        work_root = (
            Path(cache_home) if cache_home else Path.home() / ".cache"
        ) / "htc-harness"
    return BuildLayout(resource_root=resource_root, work_root=work_root)


def load_patch_manifest(
    manifest_path: Path, mode: str = "rust"
) -> tuple[str, str, list[str]]:
    if not manifest_path.exists():
        raise click.ClickException(f"Missing manifest: {manifest_path}")
    data = json.loads(manifest_path.read_text())
    cfg = data.get(mode) or {}
    repo_url = cfg.get("repo_url")
    commit = cfg.get("commit")
    patches = cfg.get("patches") or []
    if not repo_url or not commit:
        raise click.ClickException(
            f"Missing repo_url or commit in manifest for mode={mode!r}"
        )
    if not isinstance(patches, list):
        raise click.ClickException(
            f"Invalid patches list in manifest for mode={mode!r}"
        )
    return repo_url, commit, patches


def run_checked(command: list[str], cwd: Path | None) -> None:
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            cwd=cwd,
        )
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        raise click.ClickException(output.strip() or str(exc)) from exc


def patched_manifest_path_for_bin(hem_bin: Path) -> Path | None:
    try:
        parts = hem_bin.resolve().parts
    except FileNotFoundError:
        parts = hem_bin.parts
    if ".patch-workdir" not in parts:
        return None
    idx = parts.index(".patch-workdir")
    if idx + 1 >= len(parts):
        return None
    work_dir = Path(*parts[: idx + 2])
    return work_dir / "patched_manifest.json"


def _existing_build_is_current(
    work_dir: Path, desired_payload: dict, binary: Path
) -> bool:
    """True if work_dir already holds a build matching desired_payload."""
    existing_manifest = work_dir / "patched_manifest.json"
    if not existing_manifest.exists():
        return False
    try:
        existing_payload = json.loads(existing_manifest.read_text())
    except Exception:
        return False
    if not isinstance(existing_payload, dict):
        return False
    return (
        existing_payload.get("repo_url") == desired_payload["repo_url"]
        and existing_payload.get("commit") == desired_payload["commit"]
        and existing_payload.get("patches") == desired_payload["patches"]
        and binary.exists()
    )


def _prepare_patched_checkout(
    *,
    repo_root: Path,
    work_dir: Path,
    repo_url: str,
    commit: str,
    patches: list[str],
) -> None:
    """Clone repo_url at commit into work_dir and apply patches in order."""
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    run_checked(["git", "init"], cwd=work_dir)
    run_checked(["git", "remote", "add", "origin", repo_url], cwd=work_dir)
    run_checked(["git", "fetch", "--depth", "1", "origin", commit], cwd=work_dir)
    run_checked(["git", "checkout", commit], cwd=work_dir)

    for patch_rel in patches:
        patch_path = repo_root / patch_rel
        if not patch_path.exists():
            raise click.ClickException(f"Missing patch file: {patch_path}")
        run_checked(["git", "apply", str(patch_path)], cwd=work_dir)


def build_patched_rust_hem(
    resource_root: Path, *, work_root: Path | None = None, clean: bool = False
) -> Path:
    manifest_path = resource_root / "patches" / "manifest.json"
    repo_url, commit, patches = load_patch_manifest(manifest_path, "rust")
    work_dir = (work_root or resource_root) / ".patch-workdir" / "rust"
    hem_bin = work_dir / "target" / "release" / "hem"
    desired_manifest_payload = {
        "repo_url": repo_url,
        "commit": commit,
        "patches": patches,
    }

    if (
        work_dir.exists()
        and not clean
        and _existing_build_is_current(work_dir, desired_manifest_payload, hem_bin)
    ):
        click.echo("Reusing existing patched Rust HEM build.")
        return hem_bin

    _prepare_patched_checkout(
        repo_root=resource_root,
        work_dir=work_dir,
        repo_url=repo_url,
        commit=commit,
        patches=patches,
    )

    (work_dir / "patched_manifest.json").write_text(
        json.dumps(desired_manifest_payload, indent=2)
    )

    click.echo("Building Rust HEM (release)...")
    run_checked(["cargo", "build", "--release", "--features", "clap"], cwd=work_dir)
    if not hem_bin.exists():
        raise click.ClickException(f"Built hem binary not found at {hem_bin}")
    return hem_bin


# The FHS wrapper depends on home-energy-model by git URL. We must redirect that
# to our patched engine checkout, otherwise the FHS binary would embed a
# different, unpatched engine and would not emit the enhanced static output h3
# relies on.
_ENGINE_GIT_URL = "https://github.com/communitiesuk/epb-home-energy-model.git"


def _redirect_fhs_engine_dependency(fhs_dir: Path, engine_dir: Path) -> None:
    cargo_toml = fhs_dir / "Cargo.toml"
    if not cargo_toml.exists():
        raise click.ClickException(f"FHS Cargo.toml not found at {cargo_toml}")
    text = cargo_toml.read_text()
    marker = f'[patch."{_ENGINE_GIT_URL}"]'
    if marker in text:
        return
    text += (
        f"\n\n# Added by h3 build-hem: resolve the FHS wrapper's engine dependency to\n"
        f"# the patched engine checkout, so both share one build.\n"
        f"{marker}\n"
        f'home-energy-model = {{ path = "{engine_dir.as_posix()}" }}\n'
    )
    cargo_toml.write_text(text)


def build_patched_fhs_wrapper(
    resource_root: Path,
    *,
    engine_dir: Path,
    work_root: Path | None = None,
    clean: bool = False,
) -> Path:
    """Build the patched Future Homes Standard wrapper binary.

    The FHS wrapper moved out of the engine repository at engine v1.0.0-alpha.
    It is now a separate crate producing a `future-homes-standard` binary, built
    against the patched engine in `engine_dir`.
    """
    manifest_path = resource_root / "patches" / "manifest.json"
    repo_url, commit, patches = load_patch_manifest(manifest_path, "fhs")
    work_dir = (work_root or resource_root) / ".patch-workdir" / "fhs"
    fhs_bin = work_dir / "target" / "release" / "future-homes-standard"
    desired_manifest_payload = {
        "repo_url": repo_url,
        "commit": commit,
        "patches": patches,
        "engine_dir": engine_dir.as_posix(),
    }

    if (
        work_dir.exists()
        and not clean
        and _existing_build_is_current(work_dir, desired_manifest_payload, fhs_bin)
    ):
        click.echo("Reusing existing patched FHS wrapper build.")
        return fhs_bin

    _prepare_patched_checkout(
        repo_root=resource_root,
        work_dir=work_dir,
        repo_url=repo_url,
        commit=commit,
        patches=patches,
    )
    _redirect_fhs_engine_dependency(work_dir, engine_dir)

    (work_dir / "patched_manifest.json").write_text(
        json.dumps(desired_manifest_payload, indent=2)
    )

    click.echo("Building FHS wrapper (release)...")
    run_checked(
        [
            "cargo",
            "build",
            "--release",
            "--features",
            "clap",
            "--bin",
            "future-homes-standard",
        ],
        cwd=work_dir,
    )
    if not fhs_bin.exists():
        raise click.ClickException(f"Built FHS binary not found at {fhs_bin}")
    return fhs_bin
