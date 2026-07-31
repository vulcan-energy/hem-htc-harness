# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
# Run HEM through the supported Rust CLI.

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class HemRunRequest:
    # Inputs needed to run a HEM model via the Rust engine or FHS wrapper.

    input_path: Path
    weather_path: Path | None
    heat_balance: bool = True
    detailed_output_heating_cooling: bool = False
    extra_flags: Sequence[str] = field(default_factory=tuple)
    hem_bin: Path | None = None
    working_dir: Path | None = None
    dry_run: bool = False
    env_overrides: Mapping[str, str] | None = None
    # From engine v1.0.0-alpha the FHS wrapper is a separate repository and a
    # separate binary; the core engine no longer accepts --future-homes-standard.
    fhs_assumptions: bool = False
    # Where the FHS wrapper lives, when it is not beside the engine in the
    # layout `h3 build-hem` produces. The WASM build is one artifact serving
    # both, so the browser passes the engine path here.
    fhs_bin: Path | None = None
    # Stop after the wrapper's preprocessing and write the resulting model as
    # `<key>__preproc.json` instead of simulating. Only meaningful with
    # `fhs_assumptions`; the core engine has no preprocessing step to stop
    # after. See `workflow.run_offsets_batch`.
    preprocess_only: bool = False


# h3 builds both binaries side by side under .patch-workdir/.
FHS_BIN_NAME = "future-homes-standard"
_BUILD_SUBPATH = ("target", "release")


def fhs_bin_for_hem_bin(hem_bin: Path) -> Path:
    """Locate the FHS wrapper binary that pairs with a patched hem binary.

    `.patch-workdir/rust/target/release/hem`
        -> `.patch-workdir/fhs/target/release/future-homes-standard`
    """
    # hem_bin is <workdir>/rust/target/release/hem; step back over
    # release/target/rust to reach <workdir>.
    depth = len(_BUILD_SUBPATH) + 1
    if len(hem_bin.parents) <= depth:
        raise FileNotFoundError(
            f"Cannot locate the FHS wrapper binary alongside: {hem_bin}\n\n"
            "h3 expects the layout `h3 build-hem` produces,\n"
            "<workdir>/rust/target/release/hem.\n\n"
            "Fix:\n"
            "- Run `h3 build-hem` (or `h3 setup`) and use the binary it reports.\n"
            "- Or pass `--no-fhs-assumptions` to run the core engine instead."
        )
    workdir = hem_bin.parents[depth]
    return workdir.joinpath("fhs", *_BUILD_SUBPATH, FHS_BIN_NAME)


def build_command(request: HemRunRequest) -> list[str]:
    # Build the command for the supported Rust HEM/FHS engine.
    _validate_request(request)
    return _build_rust_command(request)


class HemExecutor(Protocol):
    """Executes a built HEM command.

    The seam that lets h3 run somewhere other than a local shell. The CLI uses
    `run_via_subprocess`; the browser demo supplies an executor backed by the
    HEM WASM build, since Pyodide has no `subprocess`.
    """

    def __call__(
        self,
        command: list[str],
        *,
        cwd: Path | None,
        env: Mapping[str, str],
    ) -> None:
        """Run `command`, raising on failure."""


def run_via_subprocess(
    command: list[str],
    *,
    cwd: Path | None,
    env: Mapping[str, str],
) -> None:
    """Default executor: runs HEM as a local subprocess."""
    result = subprocess.run(
        command,
        check=False,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(env),
    )
    if result.returncode != 0:
        if result.stderr:
            sys.stderr.write(result.stderr)
        raise subprocess.CalledProcessError(
            result.returncode, command, output=result.stdout, stderr=result.stderr
        )


def resolve_environment(request: HemRunRequest) -> dict[str, str]:
    """Build the environment a HEM run should execute under."""
    env = os.environ.copy()
    env.setdefault("RUST_BACKTRACE", "1")
    if request.env_overrides:
        env.update(request.env_overrides)
    return env


def run_hem(
    request: HemRunRequest,
    executor: HemExecutor | None = None,
) -> list[str]:
    """Run the HEM engine and return the command used.

    `executor` defaults to running a local subprocess. Pass a different executor
    to run HEM elsewhere, such as the WASM build inside a browser worker.
    """
    command = build_command(request)
    if request.dry_run:
        return command
    executor = executor or run_via_subprocess
    executor(command, cwd=request.working_dir, env=resolve_environment(request))
    return command


def _build_rust_command(request: HemRunRequest) -> list[str]:
    assert request.hem_bin is not None
    if request.fhs_assumptions and request.fhs_bin is not None:
        # An explicit path is the caller asserting where the wrapper is; do not
        # second-guess it against the native build layout. This is how the
        # browser says "one artifact serves both".
        binary = request.fhs_bin
    elif request.fhs_assumptions:
        binary = fhs_bin_for_hem_bin(request.hem_bin)
        if not request.dry_run and not binary.exists():
            raise FileNotFoundError(
                f"FHS wrapper binary not found: {binary}\n\n"
                "From engine v1.0.0-alpha the FHS wrapper is a separate binary.\n"
                "Fix:\n"
                "- Run `h3 build-hem` (or `h3 setup`) to build it."
            )
    else:
        binary = request.hem_bin
    command = [
        str(binary),
        str(request.input_path),
    ]
    # If no EPW is provided, HEM will use ExternalConditions from the input JSON.
    if request.weather_path is not None:
        command.extend(["-e", str(request.weather_path)])
    if request.heat_balance:
        command.append("--heat-balance")
    if request.detailed_output_heating_cooling:
        command.append("--detailed-output-heating-cooling")
    if request.fhs_assumptions:
        # Replaces the old core-engine `--future-homes-standard` flag.
        command.extend(["--mode", "actual"])
    if request.preprocess_only:
        command.append("--preprocess-only")
    command.extend(request.extra_flags)
    return command


def _validate_request(request: HemRunRequest) -> None:
    if not request.dry_run:
        if not request.input_path.exists():
            raise FileNotFoundError(f"Input file not found: {request.input_path}")
        if request.weather_path is not None and not request.weather_path.exists():
            raise FileNotFoundError(f"Weather file not found: {request.weather_path}")
    if not request.heat_balance:
        raise ValueError("heat_balance must be enabled for HTC harness runs")
    if request.preprocess_only and not request.fhs_assumptions:
        raise ValueError(
            "preprocess_only requires fhs_assumptions: only the FHS wrapper "
            "has a preprocessing step to stop after."
        )
    if request.hem_bin is None:
        raise ValueError("hem_bin is required")
