# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

from pathlib import Path

from htc_harness.config import HarnessConfig, load_config, save_config


def test_load_config_missing_returns_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "missing.json"
    config = load_config(config_path)
    assert config.hem_bin is None


def test_save_and_load_config_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config = HarnessConfig(
        hem_bin=Path("/opt/hem"),
        input_dir=Path("input"),
        weather_dir=Path("weather"),
        external_conditions_dir=Path("external_conditions"),
        output_dir=Path("output"),
    )
    save_config(config, config_path)
    loaded = load_config(config_path)
    assert loaded.hem_bin == Path("/opt/hem")
    assert loaded.input_dir == Path("input")
    assert loaded.weather_dir == Path("weather")
    assert loaded.external_conditions_dir == Path("external_conditions")
    assert loaded.output_dir == Path("output")
