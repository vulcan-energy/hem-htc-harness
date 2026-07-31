# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
# Core empirical HTC calculations.
#
# Includes:
# - Loss-term extraction from heat-balance outputs
# - Per-timestep regression across multiple offsets (primary analysis path)

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path

from .models import MeasuredHTC, Timeseries
from .parse_heat_balance import parse_heat_balance_air_node
from .reader import (
    locate_run_files,
    read_csv_timeseries,
    read_external_conditions_file,
    read_measured_htc_json,
    read_results_static,
)

# HEM renamed its heat-balance columns between engine 0.36.0-alpha and
# 1.0.0-alpha-7, from CamelCase to human-readable lowercase.
#
# Note these are the *per-timestep* column names. Each heat-balance CSV holds
# two sections: an annual-totals block whose columns are prefixed "total ", and
# the per-timestep block below it, which is what parse_heat_balance_air_node
# returns. Matching the "total " names silently finds nothing.
#
#   ThermalBridges              -> thermal_bridges
#   InfiltrationVentilation     -> infiltration_ventilation
#   FabricHeatLoss              -> fabric
#   HeatingOrCoolingSystemGains -> heating or cooling system gains
#   FabricIntHeatCool           -> fabric_int_heat_cool
#   SolarGains                  -> solar gains
#   InternalGains               -> internal gains
#   EnergyToChangeInternalTemperature
#                               -> energy to change internal temperature
#
# Legacy names are kept so h3 still reads output from the previous engine.
AIR_NODE_LOSS_SUFFIXES: tuple[str, ...] = (
    "thermal_bridges",
    "infiltration_ventilation",
    "fabric",
    "ThermalBridges",
    "InfiltrationVentilation",
    "FabricHeatLoss",
)
EXTERNAL_SYSTEM_GAINS_SUFFIX = (
    "heating or cooling system gains",
    "HeatingOrCoolingSystemGains",
)

HTC_SOURCE_PRIMARY = "air_node_q_loss"
HTC_SOURCE_SYSTEM_EXTERNAL = "system_gains_external"

DEFAULT_HEATING_COLUMN = "space heat demand"
DESIGN_HTC_LABEL = "Heat transfer coefficient"
DEFAULT_AGGREGATE_BIN_HOURS: tuple[float, ...] = (
    1.0,
    24.0,
    168.0,
    720.0,
)  # hour, day, week, month (30d)
SYSTEM_GAINS_SUFFIX = ("heating or cooling system gains", "HeatingOrCoolingSystemGains")
FABRIC_INT_HEAT_COOL_SUFFIX = ("fabric_int_heat_cool", "FabricIntHeatCool")
AIR_NODE_SOLAR_GAINS_SUFFIX = ("solar gains", "SolarGains")
AIR_NODE_INTERNAL_GAINS_SUFFIX = ("internal gains", "InternalGains")
AIR_NODE_STORAGE_SUFFIX = (
    "energy to change internal temperature",
    "EnergyToChangeInternalTemperature",
)
AIR_NODE_TEMP_INTERNAL = "temp_internal_air"
AIR_NODE_TEMP_EXTERNAL = "temp_external_air"
AIR_NODE_DELTA_T = "delta_t"
BASE_RESULTS_SERIES_SUFFIX_ALLOWLIST: tuple[str, ...] = (
    "internal air temp",
    "operative temp",
    "space heat demand",
    "solar gains",
    "internal gains",
    "Living radiator: energy output",
    "_unmet_demand: total",
    "_unmet_demand: Living",
)


def compute_q_loss_outside_series_w(air_node: Timeseries) -> list[float]:
    # Compute outside-directed heat loss power per timestep (W).
    #
    # Loss terms are negative in outputs; we return positive power for heat
    # leaving the dwelling.
    air_loss = _sum_columns_series_loss_w(air_node, suffixes=AIR_NODE_LOSS_SUFFIXES)
    if air_loss is None:
        raise ValueError("No air-node loss columns found")
    return air_loss


def compute_q_loss_components_series_w(air_node: Timeseries) -> dict[str, list[float]]:
    # Compute outside-directed heat loss power components per timestep (W).
    #
    # Returns a mapping term->series, where each series is positive for heat leaving
    # the dwelling. Terms are summed across any zones present in the CSV.
    out: dict[str, list[float]] = {}
    for term in AIR_NODE_LOSS_SUFFIXES:
        values = _sum_columns_series_loss_w(air_node, suffixes=[term])
        if values is not None:
            out[term] = values
    return out


def _sum_columns_series_w(
    timeseries: Timeseries, *, suffix: str | tuple[str, ...]
) -> list[float] | None:
    # Sum all columns ending with suffix (or any of a tuple of suffixes, which
    # is how the engine's old and new column names are both matched).
    # Returns None if no matching columns exist.
    indices: list[int] = []
    for idx, name in enumerate(timeseries.columns):
        if ":" not in name:
            continue
        if name.endswith(suffix):
            indices.append(idx)
    if not indices:
        return None
    values_w: list[float] = []
    for row in timeseries.rows:
        row_sum_w = 0.0
        for idx in indices:
            value = row[idx]
            if value is None:
                continue
            row_sum_w += value
        values_w.append(row_sum_w)
    return values_w


def _sum_columns_series_loss_w(
    timeseries: Timeseries, *, suffixes: Sequence[str]
) -> list[float] | None:
    # Sum all columns whose name ends with any suffix; return positive loss (W).
    indices: list[int] = []
    for idx, name in enumerate(timeseries.columns):
        if ":" not in name:
            continue
        for suffix in suffixes:
            if name.endswith(suffix):
                indices.append(idx)
                break
    if not indices:
        return None
    values_w: list[float] = []
    for row in timeseries.rows:
        row_sum_w = 0.0
        for idx in indices:
            value = row[idx]
            if value is None:
                continue
            # Loss terms are negative in outputs; convert to positive loss.
            row_sum_w += -value
        values_w.append(row_sum_w)
    return values_w


def _column_series(
    timeseries: Timeseries, column_name: str
) -> list[float | None] | None:
    # Return a single column series by exact name (or None if missing).
    try:
        idx = timeseries.columns.index(column_name)
    except ValueError:
        return None
    values: list[float | None] = []
    for row in timeseries.rows:
        value = row[idx] if idx < len(row) else None
        values.append(value if value is not None else None)
    return values


def _sum_external_boundary_system_gains_w(
    external_boundary: Timeseries,
) -> list[float] | None:
    # Sum HeatingOrCoolingSystemGains from external-boundary outputs.
    # Preserve HEM sign conventions (system gains are positive).
    return _sum_columns_series_w(external_boundary, suffix=EXTERNAL_SYSTEM_GAINS_SUFFIX)


def _extract_results_series(
    timeseries: Timeseries, *, suffix_allowlist: Sequence[str]
) -> dict[str, dict[str, object]]:
    # Extract a small set of series from results.csv-like timeseries.
    #
    # We use suffix matching to avoid hard-coding the zone prefix; for each suffix,
    # we return the first column whose name ends with that suffix.
    out: dict[str, dict[str, object]] = {}
    for suffix in suffix_allowlist:
        col_name = None
        col_idx = None
        for idx, name in enumerate(timeseries.columns):
            if name.endswith(suffix):
                col_name = name
                col_idx = idx
                break
        if col_name is None or col_idx is None:
            continue
        unit = timeseries.units[col_idx] if col_idx < len(timeseries.units) else ""
        values: list[float] = []
        for row in timeseries.rows:
            v = row[col_idx] if col_idx < len(row) else None
            values.append(float(v) if v is not None else float("nan"))
        out[suffix] = {"column": col_name, "unit": unit, "values": values}
    return out


def _sum_columns_series_signed_w(
    timeseries: Timeseries, *, suffixes: Sequence[str]
) -> list[float] | None:
    # Sum all columns whose name ends with any suffix, preserving the CSV sign convention.
    indices: list[int] = []
    for idx, name in enumerate(timeseries.columns):
        if ":" not in name:
            continue
        for suffix in suffixes:
            if name.endswith(suffix):
                indices.append(idx)
                break
    if not indices:
        return None
    values_w: list[float] = []
    for row in timeseries.rows:
        row_sum_w = 0.0
        for idx in indices:
            value = row[idx]
            if value is None:
                continue
            row_sum_w += value
        values_w.append(row_sum_w)
    return values_w


def _normalize_external_series(values: list[float], n: int) -> list[float] | None:
    if len(values) == n:
        return values
    # If hourly series is provided for a half-hourly run, upsample by repetition.
    if len(values) * 2 == n:
        expanded: list[float] = []
        for v in values:
            expanded.extend([v, v])
        return expanded
    if len(values) > n:
        return values[:n]
    return None


# Each series h3 reads from an EPW, with its 0-based data column. EPW data
# rows start after 8 header lines.
_EPW_COLUMNS = {"air_temperature": 6, "wind_speed": 20}
# The matching key in an ExternalConditions object.
_EXTERNAL_CONDITIONS_KEYS = {
    "air_temperature": "air_temperatures",
    "wind_speed": "wind_speeds",
}


def _external_series_from_epw(path: Path, column: int, n: int) -> list[float] | None:
    try:
        # EPW headers carry station names in the local encoding, so real files
        # are not always UTF-8. Only the numeric columns matter here; replacing
        # undecodable bytes keeps a Latin-1 station name from failing the run.
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    if len(lines) <= 8:
        return None
    values: list[float] = []
    for line in lines[8:]:
        if not line.strip():
            continue
        parts = line.split(",")
        if len(parts) <= column:
            continue
        try:
            values.append(float(parts[column]))
        except ValueError:
            continue
    if not values:
        return None
    return _normalize_external_series(values, n)


def _external_series_from_weather_source(
    *, weather_source: dict[str, str | None], quantity: str, n: int
) -> list[float] | None:
    """Load one weather series, from whichever source the run actually used.

    `quantity` is a key of `_EPW_COLUMNS`. Returns None whenever the series
    cannot be read: these series are diagnostic, and a missing one must not
    fail the run.
    """
    source_type = weather_source.get("type")
    path = weather_source.get("path")
    if not path:
        return None
    if source_type == "epw":
        return _external_series_from_epw(Path(path), _EPW_COLUMNS[quantity], n)
    if source_type != "external_conditions":
        return None
    try:
        ec = read_external_conditions_file(Path(path))
    except Exception:
        return None
    if not isinstance(ec, dict):
        return None
    values = ec.get(_EXTERNAL_CONDITIONS_KEYS[quantity])
    if not isinstance(values, list) or not values:
        return None
    return _normalize_external_series([float(v) for v in values], n)


def compute_design_htc(results_static: dict[str, float]) -> float:
    # Extract the design HTC from results_static.csv values.
    if DESIGN_HTC_LABEL not in results_static:
        raise ValueError(f"Missing '{DESIGN_HTC_LABEL}' in results_static.csv")
    return results_static[DESIGN_HTC_LABEL]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _linear_fit_slope_intercept(
    x: Sequence[float], y: Sequence[float]
) -> tuple[float, float]:
    # Return slope and intercept for y = a + m*x using least squares.
    if len(x) != len(y):
        raise ValueError("x and y must have same length")
    if len(x) < 2:
        raise ValueError("need at least 2 points for linear fit")
    mean_x = _mean(x)
    mean_y = _mean(y)
    var_x = sum((xi - mean_x) ** 2 for xi in x)
    if var_x == 0:
        raise ValueError("cannot fit with zero variance in x")
    cov_xy = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    m = cov_xy / var_x
    a = mean_y - m * mean_x
    return m, a


def _r2(y: Sequence[float], y_hat: Sequence[float]) -> float:
    if len(y) != len(y_hat):
        raise ValueError("y and y_hat must have same length")
    if not y:
        return 0.0
    mean_y = _mean(y)
    ss_tot = sum((yi - mean_y) ** 2 for yi in y)
    ss_res = sum((yi - yhi) ** 2 for yi, yhi in zip(y, y_hat))
    if ss_tot == 0:
        # All y are equal; treat as perfectly explained if residual is 0.
        return 1.0 if ss_res == 0 else 0.0
    return 1.0 - (ss_res / ss_tot)


def _htc_by_timestep_from_offsets(
    offsets: Sequence[float], series_by_offset: dict[float, list[float]]
) -> list[float]:
    # Compute per-timestep HTC using linear fit across offsets.
    if not offsets:
        return []
    first = series_by_offset.get(offsets[0])
    if first is None:
        return []
    n_timesteps = len(first)
    h_by_timestep: list[float] = []
    for t in range(n_timesteps):
        y = [series_by_offset[offset][t] for offset in offsets]
        m, _ = _linear_fit_slope_intercept(offsets, y)  # y = a + m*x
        h_by_timestep.append(-m)
    return h_by_timestep


def _daily_means(values: Sequence[float], timestep_hours: float) -> list[float]:
    steps = _timesteps_per_bin(timestep_hours, 24.0)
    vf = [
        (
            float(v)
            if isinstance(v, (int, float)) and math.isfinite(float(v))
            else float("nan")
        )
        for v in values
    ]
    out: list[float] = []
    for start, end in _bin_ranges(len(vf), steps):
        window = [v for v in vf[start:end] if math.isfinite(v)]
        out.append(sum(window) / len(window) if window else 0.0)
    return out


def _selected_indices(
    *,
    base_results: Timeseries | None,
    base_heat_balance_len: int,
    start_idx: int | None,
    end_idx: int | None,
    heating_only: bool,
    heating_column_suffix: str = DEFAULT_HEATING_COLUMN,
) -> list[int]:
    # Return indices to include, ensuring alignment across all runs.
    start = 0 if start_idx is None else start_idx
    end = base_heat_balance_len if end_idx is None else end_idx
    if start < 0 or end < 0 or end < start:
        raise ValueError("Invalid window indices")
    end = min(end, base_heat_balance_len)
    indices = list(range(start, end))
    if not heating_only:
        return indices
    if base_results is None:
        raise ValueError("heating_only requires base_results")
    heating_column_name = None
    for name in base_results.columns:
        if name.endswith(heating_column_suffix):
            heating_column_name = name
            break
    if heating_column_name is None:
        raise ValueError(f"Heating column not found: {heating_column_suffix}")
    col_idx = base_results.column_index(heating_column_name)
    out: list[int] = []
    for i in indices:
        if i >= len(base_results.rows):
            break
        value = base_results.rows[i][col_idx]
        if value is None:
            continue
        if value >= 0.0 + 1e-12:
            out.append(i)
    return out


def _timesteps_per_bin(timestep_hours: float, bin_hours: float) -> int:
    if bin_hours <= 0:
        raise ValueError("bin_hours must be > 0")
    steps = int(round(bin_hours / timestep_hours))
    if steps <= 0:
        raise ValueError("bin_hours too small for timestep_hours")
    if abs((steps * timestep_hours) - bin_hours) > 1e-9:
        raise ValueError(
            f"bin_hours ({bin_hours}) must be a multiple of timestep_hours ({timestep_hours})"
        )
    return steps


def _bin_ranges(n: int, steps: int) -> list[tuple[int, int]]:
    # Return (start,end) ranges in [0,n), end exclusive.
    if n < 0:
        raise ValueError("n must be >= 0")
    if steps <= 0:
        raise ValueError("steps must be > 0")
    out: list[tuple[int, int]] = []
    start = 0
    while start < n:
        end = min(start + steps, n)
        out.append((start, end))
        start = end
    return out


def _steps_per_day(timestep_hours: float) -> int:
    steps = int(round(24.0 / timestep_hours))
    if steps <= 0:
        raise ValueError("timestep_hours must be > 0")
    if abs((steps * timestep_hours) - 24.0) > 1e-9:
        raise ValueError("timestep_hours must divide evenly into 24 hours")
    return steps


def _calendar_month_days() -> list[tuple[str, int]]:
    return [
        ("Jan", 31),
        ("Feb", 28),
        ("Mar", 31),
        ("Apr", 30),
        ("May", 31),
        ("Jun", 30),
        ("Jul", 31),
        ("Aug", 31),
        ("Sep", 30),
        ("Oct", 31),
        ("Nov", 30),
        ("Dec", 31),
    ]


def _month_bins_from_selected(
    selected_indices: list[int], *, timestep_hours: float
) -> tuple[list[dict[str, object]], dict[str, object]]:
    steps_per_day = _steps_per_day(timestep_hours)
    month_days = _calendar_month_days()
    bins: list[dict[str, object]] = []
    if not selected_indices:
        return bins, {
            "steps_per_day": steps_per_day,
            "calendar_days": 365,
        }
    current_start = 0
    current_month = None
    current_year = None
    for i, idx in enumerate(selected_indices):
        day_index = idx // steps_per_day
        year_index = day_index // 365
        day_in_year = day_index % 365
        month_idx = 0
        day_cursor = 0
        for m_idx, (_, days) in enumerate(month_days):
            if day_in_year < day_cursor + days:
                month_idx = m_idx
                break
            day_cursor += days
        if current_month is None:
            current_month = month_idx
            current_year = year_index
        if month_idx != current_month or year_index != current_year:
            month_label = month_days[current_month][0]
            bins.append(
                {
                    "start": current_start,
                    "end": i,
                    "month": current_month + 1,
                    "year_index": current_year + 1,
                    "label": month_label,
                }
            )
            current_start = i
            current_month = month_idx
            current_year = year_index
    if current_month is not None and current_year is not None:
        month_label = month_days[current_month][0]
        bins.append(
            {
                "start": current_start,
                "end": len(selected_indices),
                "month": current_month + 1,
                "year_index": current_year + 1,
                "label": month_label,
            }
        )
    return bins, {
        "steps_per_day": steps_per_day,
        "calendar_days": 365,
    }


def _timestep_calendar_series(
    selected_indices: list[int], *, timestep_hours: float
) -> dict[str, list[int]]:
    steps_per_day = _steps_per_day(timestep_hours)
    day_index = [idx // steps_per_day for idx in selected_indices]
    hour_index = [idx % steps_per_day for idx in selected_indices]
    year_index = [d // 365 for d in day_index]
    day_of_year = [d % 365 for d in day_index]
    return {
        "timestep_day_index": day_index,
        "timestep_hour_index": hour_index,
        "timestep_year_index": year_index,
        "timestep_day_of_year": day_of_year,
    }


def _htc_components_from_q_loss(
    q_loss_components: dict[str, list[float]],
    delta_t: Sequence[float | None],
) -> tuple[dict[str, list[float | None]], dict[str, float]]:
    """Divide each loss component by delta-T to get a per-component HTC series.

    Timesteps with no delta-T, or one too close to zero to divide by, yield
    None and are excluded from the mean. Called twice per offset: once from the
    raw components, and again after the FIHC correction changes FabricHeatLoss.
    """
    components: dict[str, list[float | None]] = {}
    means: dict[str, float] = {}
    for name, values in q_loss_components.items():
        series: list[float | None] = []
        for i, q_val in enumerate(values):
            dt = delta_t[i] if i < len(delta_t) else None
            if dt is None or abs(dt) < 1e-9:
                series.append(None)
            else:
                series.append(q_val / dt)
        components[name] = series
        valid = [v for v in series if v is not None]
        means[name] = sum(valid) / len(valid) if valid else 0.0
    return components, means


def analyze_offsets(
    *,
    results_dirs_by_offset_k: dict[float, Path],
    timestep_hours: float,
    start_idx: int | None = None,
    end_idx: int | None = None,
    heating_only: bool = False,
    measured_htc_path: Path | None = None,
    schema_version: str = "0.1",
    include_fihc: bool = True,
) -> dict[str, object]:
    # Analyze an existing set of base+offset run output directories.
    # Produces a summary payload aligned to `docs/methodology.md`.
    if 0.0 not in results_dirs_by_offset_k:
        raise ValueError("results_dirs_by_offset_k must include base offset 0.0")
    offsets = sorted(results_dirs_by_offset_k.keys())
    x = offsets

    measured: MeasuredHTC | None = None
    if measured_htc_path is not None:
        measured = read_measured_htc_json(measured_htc_path)

    # If caller did not provide an explicit window, and measured HTC contains an
    # index window, apply it automatically so comparison is aligned by default.
    if (
        start_idx is None
        and end_idx is None
        and measured
        and isinstance(measured.window, dict)
    ):
        if measured.window.get("type") == "index":
            try:
                start_idx = int(measured.window.get("start_idx"))  # type: ignore[arg-type]
                end_idx = int(measured.window.get("end_idx"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                raise ValueError(
                    "Measured HTC window index must include integer start_idx and end_idx"
                )
            tw = measured.window.get("timestep_hours")
            if tw is not None:
                try:
                    twf = float(tw)
                except (TypeError, ValueError):
                    raise ValueError(
                        "Measured HTC window timestep_hours must be a number"
                    )
                if abs(twf - timestep_hours) > 1e-9:
                    raise ValueError(
                        f"Measured HTC timestep_hours ({twf}) does not match analysis timestep_hours ({timestep_hours})"
                    )

    base_dir = results_dirs_by_offset_k[0.0]
    base_files = locate_run_files(base_dir)
    base_heat_balance = parse_heat_balance_air_node(
        base_files.heat_balance_air_node_csv
    )
    base_results = None
    base_results_all = None
    base_results_selected: dict[str, dict[str, object]] = {}
    if heating_only:
        if base_files.results_csv is None:
            raise ValueError(
                "heating_only requires results.csv (or *__results.csv) in the base run directory"
            )
        base_results = read_csv_timeseries(base_files.results_csv, has_units=True)
    if base_files.results_csv is not None:
        base_results_all = read_csv_timeseries(base_files.results_csv, has_units=True)
    selected = _selected_indices(
        base_results=base_results,
        base_heat_balance_len=len(base_heat_balance.rows),
        start_idx=start_idx,
        end_idx=end_idx,
        heating_only=heating_only,
    )
    if not selected:
        raise ValueError("Selected window/filter produced zero timesteps")

    # Every run must be long enough to index. Computed here, not inside the
    # results.csv branch below, because the per-offset loop needs it too and
    # results.csv is optional.
    max_selected = max(selected)

    if base_results_all is not None:
        extracted = _extract_results_series(
            base_results_all, suffix_allowlist=BASE_RESULTS_SERIES_SUFFIX_ALLOWLIST
        )
        # Align to selected indices (best-effort; some series may be shorter).
        for suffix, payload in extracted.items():
            values = payload.get("values")
            if not isinstance(values, list):
                continue
            if len(values) < max_selected + 1:
                continue
            base_results_selected[suffix] = {
                "column": payload.get("column"),
                "unit": payload.get("unit"),
                "values": [values[i] for i in selected],
            }

    def _infer_input_model_path() -> str | None:
        parent = base_dir.parent
        # Our run layout uses <input_stem>__results as results directory name.
        input_stem = base_dir.name
        if input_stem.endswith("__results"):
            input_stem = input_stem[: -len("__results")]
        candidate = parent / f"{input_stem}.json"
        if candidate.exists():
            return str(candidate)
        # Best-effort fallback for analysis of arbitrary directories.
        matches = sorted(parent.glob("*.json"))
        return str(matches[0]) if matches else None

    def _infer_weather_source() -> dict[str, str | None]:
        parent = base_dir.parent
        # Prefer EPW if present in the run folder.
        epws = sorted(parent.glob("*.epw"))
        if epws:
            return {"type": "epw", "path": str(epws[0])}
        # If no EPW was used, try to identify an ExternalConditions file copied into the run folder.
        input_model = _infer_input_model_path()
        candidates = sorted(parent.glob("*.json"))
        if input_model:
            candidates = [p for p in candidates if str(p) != input_model]
        if candidates:
            return {"type": "external_conditions", "path": str(candidates[0])}
        # Unknown run layout: keep schema-compatible default.
        return {"type": "epw", "path": None}

    weather_source = _infer_weather_source()
    internal_selected = None
    internal_payload = base_results_selected.get("internal air temp")
    if isinstance(internal_payload, dict) and isinstance(
        internal_payload.get("values"), list
    ):
        internal_selected = [float(v) for v in internal_payload["values"]]
    external_full = _external_series_from_weather_source(
        weather_source=weather_source,
        quantity="air_temperature",
        n=len(base_heat_balance.rows),
    )
    external_selected = (
        [external_full[i] for i in selected]
        if isinstance(external_full, list)
        else None
    )
    delta_t_selected_fallback = None
    if internal_selected is not None and external_selected is not None:
        n_dt = min(len(internal_selected), len(external_selected))
        delta_t_selected_fallback = [
            internal_selected[i] - external_selected[i] for i in range(n_dt)
        ]

    # Read per-run q_loss series (W), aligned by index.
    q_loss_by_offset: dict[float, list[float]] = {}
    q_loss_components_by_offset: dict[float, dict[str, list[float]]] = {}
    q_loss_by_offset_by_source: dict[str, dict[float, list[float]]] = {
        HTC_SOURCE_PRIMARY: {},
        HTC_SOURCE_SYSTEM_EXTERNAL: {},
    }
    q_system_gains_by_offset: dict[float, list[float]] = {}
    q_system_fabric_int_by_offset: dict[float, list[float]] = {}
    q_system_total_by_offset: dict[float, list[float]] = {}
    htc_components_by_offset: dict[float, dict[str, list[float | None]]] = {}
    htc_components_mean_by_offset: dict[float, dict[str, float]] = {}
    air_node_energy_balance: dict[str, object] = {}
    for offset in offsets:
        run_dir = results_dirs_by_offset_k[offset]
        files = locate_run_files(run_dir)
        air_node = parse_heat_balance_air_node(files.heat_balance_air_node_csv)
        series_w = compute_q_loss_outside_series_w(air_node)
        if len(series_w) < max_selected + 1:
            raise ValueError(f"Run {run_dir} has fewer timesteps than expected")
        q_loss_by_offset[offset] = [series_w[i] for i in selected]
        q_loss_by_offset_by_source[HTC_SOURCE_PRIMARY][offset] = [
            series_w[i] for i in selected
        ]

        components = compute_q_loss_components_series_w(air_node)
        q_loss_components_by_offset[offset] = {
            k: [v[i] for i in selected] for k, v in components.items()
        }

        delta_t_series = _column_series(air_node, AIR_NODE_DELTA_T)
        if delta_t_series is None:
            t_int = _column_series(air_node, AIR_NODE_TEMP_INTERNAL)
            t_ext = _column_series(air_node, AIR_NODE_TEMP_EXTERNAL)
            if t_int is not None and t_ext is not None:
                delta_t_series = [
                    (
                        (t_int[i] - t_ext[i])
                        if t_int[i] is not None and t_ext[i] is not None
                        else None
                    )
                    for i in range(min(len(t_int), len(t_ext)))
                ]
        if delta_t_series is not None:
            delta_t_selected = [delta_t_series[i] for i in selected]
        else:
            delta_t_selected = delta_t_selected_fallback
        if delta_t_selected is not None:
            (
                htc_components_by_offset[offset],
                htc_components_mean_by_offset[offset],
            ) = _htc_components_from_q_loss(
                q_loss_components_by_offset[offset], delta_t_selected
            )

        # Alternate HTC sources (aligned to selected indices).
        if files.heat_balance_external_boundary_csv is not None:
            external_boundary = parse_heat_balance_air_node(
                files.heat_balance_external_boundary_csv
            )
            system_ext = _sum_external_boundary_system_gains_w(external_boundary)
            if system_ext is not None:
                if len(system_ext) < max_selected + 1:
                    raise ValueError(
                        f"Run {run_dir} has fewer timesteps than expected for external system gains"
                    )
                q_loss_by_offset_by_source[HTC_SOURCE_SYSTEM_EXTERNAL][offset] = [
                    system_ext[i] for i in selected
                ]

        system_gains = _sum_columns_series_w(air_node, suffix=SYSTEM_GAINS_SUFFIX)
        if system_gains is not None:
            q_system_gains_by_offset[offset] = [system_gains[i] for i in selected]

        if files.heat_balance_internal_boundary_csv is not None:
            internal = parse_heat_balance_air_node(
                files.heat_balance_internal_boundary_csv
            )
            fabric_int = _sum_columns_series_w(
                internal, suffix=FABRIC_INT_HEAT_COOL_SUFFIX
            )
            if fabric_int is not None:
                q_system_fabric_int_by_offset[offset] = [
                    fabric_int[i] for i in selected
                ]

        # --- FIHC correction --------------------------------------------------
        # The air-node FabricHeatLoss only captures convective heat transfer
        # from the air node to internal surfaces.  For emitters with a
        # radiative fraction (e.g. wet radiators, f_hc_c = 0.4), 60 % of
        # heating output goes directly to fabric surfaces as FabricIntHeatCool
        # and never appears in the air-node balance.  Without this correction
        # the regression under-estimates HTC by a factor of ~f_hc_c.
        #
        # We add FabricIntHeatCool to q_loss (total and FabricHeatLoss
        # component) so that the regression signal reflects the full building
        # heat loss.  Solar and internal radiative fractions are in separate
        # HEM columns (FabricIntSol, FabricIntIntGains) and are NOT included.
        #
        # Controlled by the include_fihc flag (default True).  Set to False
        # when replicating a co-heating test methodology where heating is
        # 100 % convective (electric fan heaters).
        # -----------------------------------------------------------------------
        if include_fihc and offset in q_system_fabric_int_by_offset:
            fihc = q_system_fabric_int_by_offset[offset]
            n = min(len(q_loss_by_offset[offset]), len(fihc))
            # Add FIHC to total q_loss.
            q_loss_by_offset[offset] = [
                q_loss_by_offset[offset][i] + fihc[i] for i in range(n)
            ]
            q_loss_by_offset_by_source[HTC_SOURCE_PRIMARY][offset] = list(
                q_loss_by_offset[offset]
            )
            # Fold FIHC into the FabricHeatLoss component so the three-way
            # breakdown (Fabric / Bridges / Vent) sums to the corrected total.
            if offset in q_loss_components_by_offset:
                comp = q_loss_components_by_offset[offset]
                fabric_key = "FabricHeatLoss"
                if fabric_key in comp:
                    comp[fabric_key] = [
                        comp[fabric_key][i] + fihc[i]
                        for i in range(min(len(comp[fabric_key]), len(fihc)))
                    ]
                else:
                    comp[fabric_key] = list(fihc)
            # Re-derive the per-component HTC series for this offset so it
            # reflects the corrected FabricHeatLoss.
            if delta_t_selected is not None and offset in q_loss_components_by_offset:
                (
                    htc_components_by_offset[offset],
                    htc_components_mean_by_offset[offset],
                ) = _htc_components_from_q_loss(
                    q_loss_components_by_offset[offset], delta_t_selected
                )

        # Combine q_system terms when available.
        if (
            offset in q_system_gains_by_offset
            or offset in q_system_fabric_int_by_offset
        ):
            n = len(q_loss_by_offset[offset])
            gains = q_system_gains_by_offset.get(offset, [0.0] * n)
            fint = q_system_fabric_int_by_offset.get(offset, [0.0] * n)
            q_system_total_by_offset[offset] = [g + f for g, f in zip(gains, fint)]

        # Base-run reconciliation: air-node balance residual (uses raw HEM signs).
        if offset == 0.0:
            solar = _sum_columns_series_w(air_node, suffix=AIR_NODE_SOLAR_GAINS_SUFFIX)
            internal = _sum_columns_series_w(
                air_node, suffix=AIR_NODE_INTERNAL_GAINS_SUFFIX
            )
            storage = _sum_columns_series_w(air_node, suffix=AIR_NODE_STORAGE_SUFFIX)
            loss_signed = _sum_columns_series_signed_w(
                air_node, suffixes=list(AIR_NODE_LOSS_SUFFIXES)
            )
            system = system_gains
            if solar and internal and storage and loss_signed and system:
                # Align to selected indices.
                solar_s = [solar[i] for i in selected]
                internal_s = [internal[i] for i in selected]
                storage_s = [storage[i] for i in selected]
                loss_signed_s = [loss_signed[i] for i in selected]
                system_s = [system[i] for i in selected]
                residual = [
                    solar_s[i]
                    + internal_s[i]
                    + system_s[i]
                    + storage_s[i]
                    + loss_signed_s[i]
                    for i in range(len(solar_s))
                ]
                abs_res = [abs(v) for v in residual]
                abs_res_sorted = sorted(abs_res)
                p95 = (
                    abs_res_sorted[int(0.95 * (len(abs_res_sorted) - 1))]
                    if abs_res_sorted
                    else 0.0
                )
                air_node_energy_balance = {
                    "air_node": {
                        "solar_gains_w": solar_s,
                        "internal_gains_w": internal_s,
                        "system_gains_w": system_s,
                        "storage_w": storage_s,
                        "loss_terms_signed_w": loss_signed_s,
                        "balance_residual_w": residual,
                    },
                    "summary": {
                        "mean_abs_residual_w": (
                            sum(abs_res) / len(abs_res) if abs_res else 0.0
                        ),
                        "p95_abs_residual_w": p95,
                    },
                    "notes": (
                        "Balance residual uses raw HEM sign conventions from results_heat_balance_air_node.csv: "
                        "SolarGains + InternalGains + HeatingOrCoolingSystemGains + EnergyToChangeInternalTemperature "
                        "+ (ThermalBridges + InfiltrationVentilation + FabricHeatLoss) ≈ 0."
                    ),
                }

    # Per-timestep regression across offsets (primary source).
    n_timesteps = len(selected)
    h_by_timestep: list[float] = []
    r2_by_timestep: list[float] = []
    for t in range(n_timesteps):
        y = [q_loss_by_offset[offset][t] for offset in offsets]
        m, a = _linear_fit_slope_intercept(x, y)  # y = a + m*x
        y_hat = [a + m * xi for xi in x]
        r2_value = _r2(y, y_hat)
        h_t = -m  # match methodology: q_loss decreases as Tout increases => H positive
        h_by_timestep.append(h_t)
        r2_by_timestep.append(r2_value)

    h_empirical = _mean(h_by_timestep)
    mask, mask_reason = _mask_from_summary(
        q_system_total_by_offset={
            str(k): v for k, v in q_system_total_by_offset.items()
        },
        base_results_series=base_results_selected,
        timestep_hours=timestep_hours,
        heating_eps_w=0.0,
        unmet_eps_kwh=0.0,
    )
    h_empirical_masked = None
    if mask is not None:
        masked_series = _block_htc_from_summary(
            offsets,
            {str(k): v for k, v in q_loss_by_offset.items()},
            [(0, len(mask))],
            timestep_hours,
            mask,
        )
        if masked_series and math.isfinite(masked_series[0]):
            h_empirical_masked = masked_series[0]

    # Alternate HTC sources.
    htc_by_source: dict[str, list[float]] = {
        HTC_SOURCE_PRIMARY: h_by_timestep,
    }
    htc_mean_by_source: dict[str, float] = {
        HTC_SOURCE_PRIMARY: h_empirical,
    }
    for source_key in (HTC_SOURCE_SYSTEM_EXTERNAL,):
        series_by_offset = q_loss_by_offset_by_source.get(source_key, {})
        if not series_by_offset or any(
            offset not in series_by_offset for offset in offsets
        ):
            continue
        h_series = _htc_by_timestep_from_offsets(offsets, series_by_offset)
        htc_by_source[source_key] = h_series
        htc_mean_by_source[source_key] = _mean(h_series)

    # Aggregate-by-time regression across offsets.
    h_by_bin_hours: dict[str, list[float]] = {}
    r2_by_bin_hours: dict[str, list[float]] = {}
    bin_start_positions_by_bin_hours: dict[str, list[int]] = {}
    bin_start_indices_by_bin_hours: dict[str, list[int]] = {}
    for bin_hours in DEFAULT_AGGREGATE_BIN_HOURS:
        steps = _timesteps_per_bin(timestep_hours, bin_hours)
        ranges = _bin_ranges(n_timesteps, steps)
        key = (
            str(int(bin_hours))
            if abs(bin_hours - int(bin_hours)) < 1e-9
            else str(bin_hours)
        )
        h_bins: list[float] = []
        r2_bins: list[float] = []
        start_positions: list[int] = []
        start_indices: list[int] = []
        for start, end in ranges:
            # Mean q_loss power in this bin, for each offset.
            y: list[float] = []
            for offset in offsets:
                window = q_loss_by_offset[offset][start:end]
                y.append(_mean(window))
            m, a = _linear_fit_slope_intercept(x, y)  # y = a + m*x
            y_hat = [a + m * xi for xi in x]
            r2_value = _r2(y, y_hat)
            h_bins.append(-m)
            r2_bins.append(r2_value)
            start_positions.append(start)
            start_indices.append(selected[start])
        h_by_bin_hours[key] = h_bins
        r2_by_bin_hours[key] = r2_bins
        bin_start_positions_by_bin_hours[key] = start_positions
        bin_start_indices_by_bin_hours[key] = start_indices

    # Design HTC from base run, including per-element fabric breakdown.
    static_values = read_results_static(base_files.results_static_csv)
    h_design = compute_design_htc(static_values)

    # Extract per-element fabric breakdown and component HTC from static values.
    fabric_elements = {
        k: v for k, v in static_values.items() if k.startswith("Fabric element:")
    }
    design_static = {
        "htc_total_w_per_k": static_values.get("Heat transfer coefficient"),
        "fabric_w_per_k": static_values.get("Fabric heat transfer coefficient"),
        "thermal_bridges_w_per_k": static_values.get(
            "Thermal bridges heat transfer coefficient"
        ),
        "ventilation_w_per_k": static_values.get(
            "Ventilation heat transfer coefficient"
        ),
        "ach_static": static_values.get("Air changes per hour (static)"),
        "internal_air_temp_c": static_values.get("Internal air temperature"),
        "external_air_temp_c": static_values.get("External air temperature"),
        "fabric_elements": fabric_elements,
    }

    calibration_ratio = (
        (measured.htc_value_w_per_k / h_empirical)
        if measured and h_empirical != 0
        else None
    )

    month_bins, _calendar = _month_bins_from_selected(
        selected, timestep_hours=timestep_hours
    )
    payload: dict[str, object] = {
        "schema_version": schema_version,
        "job": {
            "input_model": _infer_input_model_path(),
            "weather_source": weather_source,
            "offsets_k": offsets,
            "window": {
                "type": "index",
                "start_idx": start_idx if start_idx is not None else 0,
                "end_idx": (
                    end_idx if end_idx is not None else len(base_heat_balance.rows)
                ),
                "timestep_hours": timestep_hours,
                "heating_only": heating_only,
                "rows_used": n_timesteps,
            },
        },
        "measured_htc": (
            None
            if measured is None
            else {
                "htc_value_w_per_k": measured.htc_value_w_per_k,
                "window": measured.window,
                "method_profile": (
                    None
                    if measured.method_profile is None
                    else measured.method_profile.__dict__
                ),
                "source": measured.source,
                "notes": measured.notes,
            }
        ),
        "runs": [
            {
                "offset_k": offset,
                "run_dir": str(results_dirs_by_offset_k[offset].parent),
                "results_dir": str(results_dirs_by_offset_k[offset]),
            }
            for offset in offsets
        ],
        "summary": {
            "htc_empirical_w_per_k": h_empirical,
            "htc_empirical_masked_w_per_k": h_empirical_masked,
            "htc_empirical_by_source": htc_mean_by_source,
            "htc_empirical_source_primary": HTC_SOURCE_PRIMARY,
            "htc_design_w_per_k": h_design,
            "design_static": design_static,
            "calibration_ratio_measured_over_empirical": calibration_ratio,
            "mean_r2": _mean(r2_by_timestep),
        },
        "derived": {
            "htc_w_per_k_by_timestep": h_by_timestep,
            "htc_w_per_k_by_timestep_by_source": htc_by_source,
            "r2_by_timestep": r2_by_timestep,
            "selected_indices": selected,
            # Per-offset total loss series (W), aligned to selected_indices.
            # This includes air-node convective losses (FabricHeatLoss,
            # ThermalBridges, InfiltrationVentilation) PLUS the radiative
            # heating component FabricIntHeatCool from the internal-boundary
            # heat balance.  The FIHC term is folded into FabricHeatLoss so
            # the three-way component split still sums to the total.
            "q_loss_w_by_offset_k": {
                str(offset): q_loss_by_offset[offset] for offset in offsets
            },
            "q_loss_w_by_offset_k_by_source": {
                source: {str(k): v for k, v in series.items()}
                for source, series in q_loss_by_offset_by_source.items()
            },
            # Loss components by type (W), aligned to selected_indices.
            # FabricHeatLoss = air-node convective fabric loss + FabricIntHeatCool
            # (radiative heating to internal surfaces).
            "q_loss_components_w_by_offset_k": {
                str(offset): q_loss_components_by_offset[offset] for offset in offsets
            },
            "hem_htc_components_w_per_k_by_offset_k": {
                str(k): v for k, v in htc_components_by_offset.items()
            },
            "hem_htc_components_mean_w_per_k_by_offset_k": {
                str(k): v for k, v in htc_components_mean_by_offset.items()
            },
            # System heat-flow proxies (best-effort): aligned to selected_indices.
            #
            # These come from:
            # - air-node: *: HeatingOrCoolingSystemGains
            # - internal-boundary: *: FabricIntHeatCool
            # No sign normalization is applied (we preserve HEM sign conventions).
            "q_system_fabric_int_heat_cool_w_by_offset_k": {
                str(k): v for k, v in q_system_fabric_int_by_offset.items()
            },
            "q_system_total_w_by_offset_k": {
                str(k): v for k, v in q_system_total_by_offset.items()
            },
            "air_node_energy_balance": air_node_energy_balance,
            # Optional base-run results.csv series aligned to selected_indices.
            "base_results_series": base_results_selected,
            "temperature_series": {
                "internal_air_temp_c": base_results_selected.get("internal air temp"),
                "external_air_temp_c": _external_series_from_weather_source(
                    weather_source=weather_source,
                    quantity="air_temperature",
                    n=len(base_heat_balance.rows),
                ),
                "wind_speed_ms": _external_series_from_weather_source(
                    weather_source=weather_source,
                    quantity="wind_speed",
                    n=len(base_heat_balance.rows),
                ),
            },
            # Aggregate-by-time HTC/R² series keyed by bin size (hours).
            "htc_w_per_k_by_bin_hours": h_by_bin_hours,
            "r2_by_bin_hours": r2_by_bin_hours,
            # Bin start positions in the selected array (0..rows_used-1).
            "bin_start_positions_by_bin_hours": bin_start_positions_by_bin_hours,
            # Bin start indices in the original (unfiltered) run.
            "bin_start_indices_by_bin_hours": bin_start_indices_by_bin_hours,
            # Calendar-aligned monthly bins derived from selected indices.
            "month_bins": month_bins,
        },
    }
    return payload


def _apply_block_gating(
    mask: list[bool],
    timestep_hours: float,
    block_hours: float,
    valid_min_fraction: float,
) -> list[bool]:
    """Zero out entire blocks where the valid-fraction is below threshold.

    Mirrors the daily-gating logic used in :class:`PlotSettings`.
    """
    steps = _timesteps_per_bin(timestep_hours, block_hours)
    ranges = _bin_ranges(len(mask), steps)
    fractions = _valid_fraction(mask, ranges)
    gated = mask[:]
    for (start, end), frac in zip(ranges, fractions):
        if frac >= valid_min_fraction:
            continue
        for i in range(start, min(end, len(gated))):
            gated[i] = False
    return gated


def extract_mask_from_summary(
    payload: dict[str, object],
    *,
    heating_eps_w: float = 0.0,
    unmet_eps_kwh: float = 0.0,
    mask_block_hours: float | None = 24.0,
    valid_min_fraction: float = 0.95,
) -> tuple[list[bool] | None, str]:
    """Build a per-timestep mask from a summary payload.

    Returns (mask, reason).  *mask* is ``None`` when the required series
    are missing; *reason* then explains why.

    When *mask_block_hours* is not ``None``, daily (or other block-level)
    gating is applied: blocks where fewer than *valid_min_fraction* of
    timesteps pass the per-timestep mask are zeroed out entirely.
    """
    derived = payload.get("derived") or {}
    job = payload.get("job") or {}
    window = job.get("window") or {}
    q_system_total_by_offset = derived.get("q_system_total_w_by_offset_k") or {}
    base_results_series = derived.get("base_results_series") or {}
    timestep_raw = window.get("timestep_hours")
    timestep_hours = float(timestep_raw) if timestep_raw is not None else 0.5
    mask, reason = _mask_from_summary(
        q_system_total_by_offset=q_system_total_by_offset,
        base_results_series=base_results_series,
        timestep_hours=timestep_hours,
        heating_eps_w=heating_eps_w,
        unmet_eps_kwh=unmet_eps_kwh,
    )
    if mask is not None and mask_block_hours is not None and mask_block_hours > 0:
        mask = _apply_block_gating(
            mask, timestep_hours, mask_block_hours, valid_min_fraction
        )
    return mask, reason


def masked_monthly_htc_from_summary(
    payload: dict[str, object],
    *,
    heating_eps_w: float = 0.0,
    unmet_eps_kwh: float = 0.0,
    valid_min_fraction: float = 0.95,
    mask_block_hours: float | None = 24.0,
    mask_override: list[bool] | None = None,
    aggregate_stat: str = "mean",
) -> dict[str, object]:
    """Compute masked monthly aggregate HTC from a summary payload.

    When *mask_override* is provided the per-timestep mask is taken as-is
    instead of being recomputed from this payload's heating/unmet series.
    This ensures all calibration runs are compared over the same timesteps.

    *mask_block_hours* enables daily block gating (default 24 h): blocks
    where fewer than *valid_min_fraction* timesteps pass are zeroed out.
    """
    job = payload.get("job") or {}
    derived = payload.get("derived") or {}
    window = job.get("window") or {}
    offsets = job.get("offsets_k") or []
    month_bins = derived.get("month_bins") or []
    q_loss_by_offset = derived.get("q_loss_w_by_offset_k") or {}
    timestep_raw = window.get("timestep_hours")
    timestep_hours = float(timestep_raw) if timestep_raw is not None else 0.5

    stat = aggregate_stat.lower()
    if stat not in {"mean", "median"}:
        stat = "mean"

    if not isinstance(offsets, list) or not isinstance(q_loss_by_offset, dict):
        return {
            "masked_aggregate_w_per_k": None,
            "masked_aggregate_stat": stat,
            "masked_median_w_per_k": None,
            "masked_mean_w_per_k": None,
            "monthly_htc_w_per_k": [],
            "labels": [],
            "mask_reason": "Missing offsets or q_loss series",
            "mask_diagnostics": {
                "n_timesteps_total": 0,
                "n_timesteps_valid": 0,
                "n_months_total": 0,
                "n_months_finite": 0,
                "valid_fraction_by_month": [],
            },
        }
    if not isinstance(month_bins, list) or not month_bins:
        return {
            "masked_aggregate_w_per_k": None,
            "masked_aggregate_stat": stat,
            "masked_median_w_per_k": None,
            "masked_mean_w_per_k": None,
            "monthly_htc_w_per_k": [],
            "labels": [],
            "mask_reason": "Missing month bins",
            "mask_diagnostics": {
                "n_timesteps_total": 0,
                "n_timesteps_valid": 0,
                "n_months_total": 0,
                "n_months_finite": 0,
                "valid_fraction_by_month": [],
            },
        }

    offsets_f = [float(x) for x in offsets if isinstance(x, (int, float))]
    ranges: list[tuple[int, int]] = []
    labels: list[str] = []
    for idx, entry in enumerate(month_bins):
        if not isinstance(entry, dict):
            continue
        start = entry.get("start")
        end = entry.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        ranges.append((start, end))
        label = entry.get("label")
        labels.append(str(label) if label else f"month_{idx + 1}")

    if mask_override is not None:
        mask: list[bool] | None = mask_override
        mask_reason = ""
    else:
        q_system_total_by_offset = derived.get("q_system_total_w_by_offset_k") or {}
        base_results_series = derived.get("base_results_series") or {}
        mask, mask_reason = _mask_from_summary(
            q_system_total_by_offset=q_system_total_by_offset,
            base_results_series=base_results_series,
            timestep_hours=timestep_hours,
            heating_eps_w=heating_eps_w,
            unmet_eps_kwh=unmet_eps_kwh,
        )
        if mask is not None and mask_block_hours is not None and mask_block_hours > 0:
            mask = _apply_block_gating(
                mask, timestep_hours, mask_block_hours, valid_min_fraction
            )
    htc_by_month = _block_htc_from_summary(
        offsets_f,
        q_loss_by_offset,
        ranges,
        timestep_hours,
        mask,
    )
    valid_fraction_by_month = _valid_fraction(mask, ranges) if mask is not None else []

    finite = [v for v in htc_by_month if math.isfinite(v)]
    median = _percentile(finite, 50) if finite else None
    mean = _mean(finite) if finite else None
    aggregate = mean if stat == "mean" else median
    if mask is not None:
        n_timesteps_total = len(mask)
    else:
        n_timesteps_total = 0
        for vv in q_loss_by_offset.values():
            if isinstance(vv, list):
                n_timesteps_total = len(vv)
                break
    n_timesteps_valid = (
        sum(1 for keep in mask if keep) if mask is not None else n_timesteps_total
    )
    if mask is None:
        reason_out = mask_reason
    elif finite:
        reason_out = ""
    else:
        months_passing = (
            sum(1 for frac in valid_fraction_by_month if frac >= valid_min_fraction)
            if valid_min_fraction is not None
            else len(valid_fraction_by_month)
        )
        reason_out = (
            "No finite monthly HTC values after masking and block gating; "
            f"{months_passing}/{len(ranges)} months meet valid_min_fraction="
            f"{valid_min_fraction:.2f}."
            if valid_min_fraction is not None
            else "No finite monthly HTC values after masking and block gating."
        )
    return {
        "masked_aggregate_w_per_k": aggregate,
        "masked_aggregate_stat": stat,
        "masked_median_w_per_k": median,
        "masked_mean_w_per_k": mean,
        "monthly_htc_w_per_k": htc_by_month,
        "labels": labels,
        "mask_reason": reason_out,
        "mask_settings": {
            "heating_eps_w": heating_eps_w,
            "unmet_eps_kwh": unmet_eps_kwh,
            "valid_min_fraction": valid_min_fraction,
            "mask_block_hours": mask_block_hours,
            "gating_stage": "block_only",
        },
        "mask_diagnostics": {
            "n_timesteps_total": n_timesteps_total,
            "n_timesteps_valid": n_timesteps_valid,
            "n_months_total": len(htc_by_month),
            "n_months_finite": len(finite),
            "valid_fraction_by_month": [
                {"label": labels[idx], "fraction": frac}
                for idx, frac in enumerate(valid_fraction_by_month)
            ],
        },
    }


def _mask_from_summary(
    *,
    q_system_total_by_offset: dict[str, object],
    base_results_series: dict[str, object],
    timestep_hours: float,
    heating_eps_w: float,
    unmet_eps_kwh: float,
) -> tuple[list[bool] | None, str]:
    heating = _get_offset_series(q_system_total_by_offset, 0.0)
    if heating is None:
        for key in ("Living radiator: energy output", "space heat demand"):
            payload = base_results_series.get(key)
            if isinstance(payload, dict) and isinstance(payload.get("values"), list):
                vals = [float(v) for v in payload["values"]]  # type: ignore[index]
                if "kWh" in str(payload.get("unit") or ""):
                    vals = [v * 1000.0 / timestep_hours for v in vals]
                heating = vals
                break
    unmet = base_results_series.get("_unmet_demand: Living") or base_results_series.get(
        "_unmet_demand: total"
    )
    unmet_values = None
    if isinstance(unmet, dict) and isinstance(unmet.get("values"), list):
        unmet_values = [float(v) for v in unmet["values"]]  # type: ignore[arg-type]
    if heating is None:
        return None, "Missing base heating proxy for masking"
    if unmet_values is None:
        return None, "Missing unmet-demand series for masking"
    n = min(len(heating), len(unmet_values))
    heating = heating[:n]
    unmet_values = unmet_values[:n]
    mask = [
        (h > heating_eps_w) and (u <= unmet_eps_kwh)
        for h, u in zip(heating, unmet_values)
    ]
    return mask, ""


def _get_offset_series(
    series_by_offset: dict[str, object], offset: float
) -> list[float] | None:
    for key in (str(offset), f"{offset:.1f}", f"{offset:.2f}"):
        series = series_by_offset.get(key)
        if isinstance(series, list):
            return [float(v) for v in series]
    return None


def _block_htc_from_summary(
    offsets: Sequence[float],
    series_by_offset: dict[str, object],
    ranges: Sequence[tuple[int, int]],
    timestep_hours: float,
    mask: list[bool] | None,
) -> list[float]:
    # Resolve each offset's series once. `_get_offset_series` coerces the whole
    # series to float, so looking it up inside the range loop re-walks every
    # timestep per block per offset — ~100x slower over a year of daily bins,
    # and this is on the report's hot path.
    series_per_offset = [_get_offset_series(series_by_offset, o) for o in offsets]

    h_blocks: list[float] = []
    for start, end in ranges:
        if mask is None:
            valid_hours = (end - start) * timestep_hours
        else:
            valid_hours = sum(1 for keep in mask[start:end] if keep) * timestep_hours
        q_vals: list[float] = []
        for series in series_per_offset:
            if series is None:
                q_vals.append(float("nan"))
                continue
            window = series[start:end]
            if mask is None:
                valid = window
            else:
                valid = [v for v, keep in zip(window, mask[start:end]) if keep]
            q_vals.append(sum(valid) * timestep_hours)
        if valid_hours <= 0 or any(not math.isfinite(v) for v in q_vals):
            h_blocks.append(float("nan"))
            continue
        try:
            m, _ = _linear_fit_slope_intercept(offsets, q_vals)
        except ValueError:
            h_blocks.append(float("nan"))
            continue
        h_blocks.append(-m / valid_hours)
    return h_blocks


def _valid_fraction(mask: list[bool], ranges: Sequence[tuple[int, int]]) -> list[float]:
    out: list[float] = []
    for start, end in ranges:
        window = mask[start:end]
        out.append(sum(1 for v in window if v) / len(window) if window else 0.0)
    return out


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    idx = int(round((pct / 100.0) * (len(xs) - 1)))
    idx = max(0, min(idx, len(xs) - 1))
    return xs[idx]


def solve_single_calibration_scalar(
    *,
    htc_baseline: float,
    htc_target: float,
    s_global: float,
    lambda_reg: float,
    scalar_bounds: tuple[float, float],
) -> dict[str, float]:
    # Closed-form regularized solve for one scalar around 1.0.
    y = htc_target - htc_baseline
    lambda_reg = max(lambda_reg, 1e-6)
    denom = s_global * s_global + lambda_reg
    d_scalar = (s_global * y) / denom if denom > 0 else 0.0
    scalar = max(scalar_bounds[0], min(1.0 + d_scalar, scalar_bounds[1]))
    d_applied = scalar - 1.0
    pred = htc_baseline + s_global * d_applied
    residual = pred - htc_target
    return {
        "scalar": scalar,
        "delta_scalar": d_applied,
        "htc_pred_w_per_k": pred,
        "residual_w_per_k": residual,
    }


def solve_single_calibration_increment(
    *,
    htc_baseline: float,
    htc_target: float,
    s_global: float,
    lambda_reg: float,
    scalar_current: float,
    scalar_bounds: tuple[float, float],
) -> dict[str, float]:
    # Closed-form regularized solve for one scalar increment around current value.
    y = htc_target - htc_baseline
    lambda_reg = max(lambda_reg, 1e-6)
    denom = s_global * s_global + lambda_reg
    d_scalar = (s_global * y) / denom if denom > 0 else 0.0
    scalar = max(scalar_bounds[0], min(scalar_current + d_scalar, scalar_bounds[1]))
    d_applied = scalar - scalar_current
    pred = htc_baseline + s_global * d_applied
    residual = pred - htc_target
    return {
        "scalar": scalar,
        "delta_scalar": d_applied,
        "htc_pred_w_per_k": pred,
        "residual_w_per_k": residual,
    }
