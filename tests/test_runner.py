# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from htc_harness.runner import HemRunRequest, build_command, run_hem


def test_build_rust_command_dry_run() -> None:
    request = HemRunRequest(
        input_path=Path("input.json"),
        weather_path=Path("weather.epw"),
        hem_bin=Path("/opt/hem"),
        heat_balance=True,
        detailed_output_heating_cooling=False,
        extra_flags=["--fhs-assumptions"],
        dry_run=True,
    )
    command = build_command(request)
    assert command == [
        "/opt/hem",
        "input.json",
        "-e",
        "weather.epw",
        "--heat-balance",
        "--fhs-assumptions",
    ]


def test_custom_executor_replaces_subprocess(tmp_path: Path) -> None:
    """HEM execution is injectable, so Pyodide can supply a WASM-backed executor."""
    model = tmp_path / "model.json"
    model.write_text("{}")
    calls: list[dict] = []

    def fake_executor(command, *, cwd, env):
        calls.append({"command": command, "cwd": cwd, "env": env})

    request = HemRunRequest(
        input_path=model,
        weather_path=None,
        hem_bin=Path("/opt/hem"),
        heat_balance=True,
        env_overrides={"HEM_ACH_CAL_FACTOR": "1.25"},
    )
    command = run_hem(request, executor=fake_executor)

    assert command == ["/opt/hem", str(model), "--heat-balance"]
    assert len(calls) == 1
    # The executor receives the fully resolved environment, including overrides.
    assert calls[0]["env"]["HEM_ACH_CAL_FACTOR"] == "1.25"
    assert calls[0]["env"]["RUST_BACKTRACE"] == "1"


def test_dry_run_does_not_call_executor() -> None:
    def exploding_executor(command, *, cwd, env):  # pragma: no cover - must not run
        raise AssertionError("executor must not be called for a dry run")

    request = HemRunRequest(
        input_path=Path("input.json"),
        weather_path=None,
        hem_bin=Path("/opt/hem"),
        heat_balance=True,
        dry_run=True,
    )
    assert run_hem(request, executor=exploding_executor) == [
        "/opt/hem",
        "input.json",
        "--heat-balance",
    ]


def test_fhs_assumptions_selects_wrapper_binary_and_mode() -> None:
    """FHS is a separate binary from engine v1.0.0-alpha, not a core-engine flag."""
    request = HemRunRequest(
        input_path=Path("input.json"),
        weather_path=Path("weather.epw"),
        hem_bin=Path("/repo/.patch-workdir/rust/target/release/hem"),
        heat_balance=True,
        fhs_assumptions=True,
        dry_run=True,
    )
    command = build_command(request)
    assert command == [
        "/repo/.patch-workdir/fhs/target/release/future-homes-standard",
        "input.json",
        "-e",
        "weather.epw",
        "--heat-balance",
        "--mode",
        "actual",
    ]


def test_without_fhs_assumptions_uses_core_engine() -> None:
    request = HemRunRequest(
        input_path=Path("input.json"),
        weather_path=None,
        hem_bin=Path("/repo/.patch-workdir/rust/target/release/hem"),
        heat_balance=True,
        dry_run=True,
    )
    command = build_command(request)
    assert command == [
        "/repo/.patch-workdir/rust/target/release/hem",
        "input.json",
        "--heat-balance",
    ]
    assert "--mode" not in command
    # The core engine no longer accepts this flag; h3 must never emit it.
    assert "--future-homes-standard" not in command


def test_fhs_bin_derived_from_hem_bin() -> None:
    from htc_harness.runner import fhs_bin_for_hem_bin

    assert fhs_bin_for_hem_bin(
        Path("/repo/.patch-workdir/rust/target/release/hem")
    ) == Path("/repo/.patch-workdir/fhs/target/release/future-homes-standard")


def test_engine_request_has_no_runner_switch() -> None:
    assert "runner" not in {field.name for field in fields(HemRunRequest)}


def test_run_hem_dry_run_returns_command() -> None:
    request = HemRunRequest(
        input_path=Path("input.json"),
        weather_path=Path("weather.epw"),
        hem_bin=Path("/opt/hem"),
        dry_run=True,
    )
    command = run_hem(request)
    assert command[0] == "/opt/hem"


def test_engine_requires_specific_binary() -> None:
    request = HemRunRequest(
        input_path=Path("input.json"),
        weather_path=Path("weather.epw"),
        dry_run=True,
    )
    with pytest.raises(ValueError):
        build_command(request)


def test_runner_rejects_heat_balance_disabled() -> None:
    request = HemRunRequest(
        input_path=Path("input.json"),
        weather_path=Path("weather.epw"),
        hem_bin=Path("/opt/hem"),
        heat_balance=False,
        dry_run=True,
    )
    with pytest.raises(ValueError):
        build_command(request)
