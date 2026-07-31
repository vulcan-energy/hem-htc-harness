# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from htc_harness.runner import HemRunRequest, run_hem

INTEGRATION_ENV = "HTC_HARNESS_RUNNER_INTEGRATION"


def _env_path(name: str, default: Path | None = None) -> Path:
    """Resolve a path from the environment.

    The demo model and weather file live in a HEM checkout, whose location is
    the caller's business. These used to default to one developer's home
    directory, which both leaked that path into a public repo and meant the
    test could only ever run on that machine.
    """
    value = os.environ.get(name)
    if value:
        return Path(value)
    if default is not None:
        return default
    pytest.skip(f"{name} is not set; point it at a HEM demo file to run this test")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


@pytest.mark.skipif(
    os.environ.get(INTEGRATION_ENV) != "1",
    reason="Set HTC_HARNESS_RUNNER_INTEGRATION=1 to run integration tests.",
)
def test_run_hem_integration(tmp_path: Path) -> None:
    input_path = _env_path("HTC_HARNESS_INPUT")
    weather_path = _env_path("HTC_HARNESS_EPW")
    hem_bin = Path(_required_env("HTC_HARNESS_HEM_BIN"))

    local_input = tmp_path / input_path.name
    local_weather = tmp_path / weather_path.name
    shutil.copy2(input_path, local_input)
    shutil.copy2(weather_path, local_weather)

    request = HemRunRequest(
        input_path=local_input,
        weather_path=local_weather,
        hem_bin=hem_bin,
        heat_balance=True,
        dry_run=False,
        working_dir=tmp_path,
    )
    run_hem(request)

    results_dir = tmp_path / f"{local_input.stem}__results"
    assert results_dir.exists()
    heat_balance_files = list(results_dir.glob("*heat_balance_air_node*.csv"))
    assert heat_balance_files
