# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.

"""Path resolution and run-directory preparation.

Extracted from `cli.py` so the workflow can be driven without `click`. No
function here starts a subprocess, so all of it runs under Pyodide.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

from .config import HarnessConfig
from .errors import HarnessError, HarnessParameterError, HarnessUsageError
from .reader import (
    merge_external_conditions_into_model,
    offset_epw_dry_bulb,
    offset_external_conditions_air_temperatures,
    read_external_conditions_file,
)

DEFAULT_OFFSETS_K = (-1.0, 1.0)


def parse_window(window: str | None) -> tuple[int | None, int | None]:
    """Parse a 'start_idx,end_idx' window string."""
    if not window:
        return None, None
    parts = [p.strip() for p in window.split(",")]
    if len(parts) != 2:
        raise HarnessParameterError("window must be 'start_idx,end_idx'")
    return (int(parts[0]) if parts[0] else None, int(parts[1]) if parts[1] else None)


def require_exists(path: Path, *, label: str) -> None:
    if path.exists():
        return
    raise HarnessError(f"{label} not found: {path}")


def resolve_path(value: Path, base_dir: Path | None) -> Path:
    """Resolve a user-provided path against an optional base directory.

    Rules (in order):
    1. Absolute paths are used as-is.
    2. If base_dir is set, resolve under base_dir.
    3. If the relative path exists relative to cwd, use it.
    4. Return value as-is (will likely fail validation later).
    """
    if value.is_absolute():
        return value
    if base_dir:
        return base_dir / value
    if value.exists():
        return value.resolve()
    return value


def resolve_and_validate_inputs(
    *,
    config: HarnessConfig,
    input_path: Path,
    weather_path: Path | None,
    external_conditions_path: Path | None,
    dry_mode: bool,
    already_resolved: bool = False,
) -> tuple[Path, Path | None, Path | None]:
    """Resolve the input triple and enforce the weather/EC exclusivity rule.

    Set `already_resolved` when the caller has resolved the paths itself, so
    validation still runs but resolution does not happen twice. Resolving twice
    is not harmless: with a *relative* configured base directory,
    `resolve_path` would prepend it again and produce `inputs/inputs/x.json`.
    """
    if already_resolved:
        resolved_input = input_path
        resolved_weather = weather_path
        resolved_external_conditions = external_conditions_path
    else:
        resolved_input = resolve_path(input_path, config.input_dir)
        resolved_weather = (
            resolve_path(weather_path, config.weather_dir) if weather_path else None
        )
        resolved_external_conditions = (
            resolve_path(external_conditions_path, config.external_conditions_dir)
            if external_conditions_path
            else None
        )

    if not dry_mode:
        require_exists(resolved_input, label="Input model")
        if resolved_weather is not None:
            require_exists(resolved_weather, label="Weather file")
        if resolved_external_conditions is not None:
            require_exists(
                resolved_external_conditions, label="External conditions file"
            )

    if resolved_weather and resolved_external_conditions:
        raise HarnessUsageError(
            "Use either --weather or --external-conditions, not both"
        )
    if not resolved_weather and not resolved_external_conditions:
        raise HarnessUsageError("One of --weather or --external-conditions is required")

    return resolved_input, resolved_weather, resolved_external_conditions


def default_run_output_root(
    config: HarnessConfig, resolved_input: Path, out_dir: Path | None
) -> Path:
    """Default to <output_root>/<input_stem> to avoid collisions across inputs."""
    base = out_dir or config.output_dir
    if not base:
        raise HarnessUsageError(
            "out-dir is required (or set output_dir in config via h3 setup)"
        )
    return base / resolved_input.stem


def format_offset_label(prefix: str, offset_k: float) -> str:
    if offset_k == 0.0:
        return f"{prefix}_base"
    sign = "plus" if offset_k > 0 else "minus"
    value = f"{abs(offset_k):.3f}".rstrip("0").rstrip(".")
    return f"{prefix}_{sign}{value}K"


def expected_results_dirs(
    *,
    output_root: Path,
    label: str,
    input_stem: str,
    offsets_list: list[float],
) -> dict[float, Path]:
    results_dirs: dict[float, Path] = {}
    for offset in offsets_list:
        run_label = format_offset_label(label, offset)
        results_dirs[offset] = output_root / run_label / f"{input_stem}__results"
    return results_dirs


def prepare_run_dir(
    out_root: Path,
    label: str,
    input_path: Path,
    weather_path: Path | None,
    external_conditions_path: Path | None,
    offset_k: float,
    model_transform: Callable[[dict[str, object]], dict[str, object]] | None = None,
) -> tuple[Path, Path, Path]:
    """Stage one offset run: copy inputs, apply the temperature offset."""
    run_dir = out_root / label
    run_dir.mkdir(parents=True, exist_ok=True)
    input_copy = run_dir / input_path.name
    shutil.copy2(input_path, input_copy)
    weather_copy: Path
    if weather_path is not None:
        weather_copy = run_dir / weather_path.name
        if offset_k == 0.0:
            shutil.copy2(weather_path, weather_copy)
        else:
            offset_epw_dry_bulb(weather_path, weather_copy, offset_k)
        if model_transform is not None:
            base_model = json.loads(input_copy.read_text())
            transformed = model_transform(base_model)
            input_copy.write_text(json.dumps(transformed))
    else:
        # No EPW: use ExternalConditions merged into the input JSON.
        assert external_conditions_path is not None  # enforced during validation
        weather_copy = run_dir / "NO_WEATHER_FILE"
        weather_copy.write_text(
            "ExternalConditions merged into input JSON; no EPW used.\n"
        )
        ec_copy = run_dir / external_conditions_path.name
        shutil.copy2(external_conditions_path, ec_copy)
        base_model = json.loads(input_copy.read_text())
        ext = read_external_conditions_file(ec_copy)
        if offset_k != 0.0:
            ext = offset_external_conditions_air_temperatures(ext, offset_k)
        merged = merge_external_conditions_into_model(
            model=base_model, external_conditions=ext, preserve_shading_segments=True
        )
        if model_transform is not None:
            merged = model_transform(merged)
        input_copy.write_text(json.dumps(merged))
    results_dir = run_dir / f"{input_copy.stem}__results"
    return input_copy, weather_copy, results_dir
