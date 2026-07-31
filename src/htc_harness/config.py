# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
# User-level configuration for htc-harness CLI defaults.

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_DIRNAME = "htc-harness"
DEFAULT_CONFIG_FILENAME = "config.json"


@dataclass(frozen=True)
class HarnessConfig:
    # CLI defaults stored in a per-user config file.

    hem_bin: Path | None = None
    input_dir: Path | None = None
    weather_dir: Path | None = None
    external_conditions_dir: Path | None = None
    output_dir: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "hem_bin": str(self.hem_bin) if self.hem_bin else None,
            "input_dir": str(self.input_dir) if self.input_dir else None,
            "weather_dir": str(self.weather_dir) if self.weather_dir else None,
            "external_conditions_dir": str(self.external_conditions_dir)
            if self.external_conditions_dir
            else None,
            "output_dir": str(self.output_dir) if self.output_dir else None,
        }

    def with_overrides(
        self,
        *,
        hem_bin: Path | None = None,
        input_dir: Path | None = None,
        weather_dir: Path | None = None,
        external_conditions_dir: Path | None = None,
        output_dir: Path | None = None,
    ) -> HarnessConfig:
        return HarnessConfig(
            hem_bin=hem_bin or self.hem_bin,
            input_dir=input_dir or self.input_dir,
            weather_dir=weather_dir or self.weather_dir,
            external_conditions_dir=external_conditions_dir
            or self.external_conditions_dir,
            output_dir=output_dir or self.output_dir,
        )


def default_config_dir() -> Path:
    env_override = os.environ.get("XDG_CONFIG_HOME")
    if env_override:
        return Path(env_override)
    return Path.home() / ".config"


def default_config_path() -> Path:
    return default_config_dir() / DEFAULT_CONFIG_DIRNAME / DEFAULT_CONFIG_FILENAME


def load_config(config_path: Path | None) -> HarnessConfig:
    resolved = config_path or default_config_path()
    if not resolved.exists():
        return HarnessConfig()
    raw = json.loads(resolved.read_text())
    return HarnessConfig(
        hem_bin=_path_from_json(raw.get("hem_bin")),
        input_dir=_path_from_json(raw.get("input_dir")),
        weather_dir=_path_from_json(raw.get("weather_dir")),
        external_conditions_dir=_path_from_json(raw.get("external_conditions_dir")),
        output_dir=_path_from_json(raw.get("output_dir")),
    )


def save_config(config: HarnessConfig, config_path: Path | None) -> Path:
    resolved = config_path or default_config_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(config.to_dict(), indent=2))
    return resolved


def _path_from_json(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value)
