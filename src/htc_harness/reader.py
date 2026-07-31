# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
# File discovery and CSV reading utilities for HEM outputs.

from __future__ import annotations

import csv
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import MeasuredConditions, MeasuredHTC, MethodProfile, RunFiles, Timeseries


def _find_single_file(run_dir: Path, patterns: Iterable[str]) -> Path:
    # Locate a single file in run_dir matching any pattern.
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(run_dir.glob(pattern))
    if len(matches) == 0:
        raise FileNotFoundError(f"No files found in {run_dir} matching {patterns}")
    if len(matches) > 1:
        raise FileExistsError(
            f"Multiple files found in {run_dir} matching {patterns}: {matches}"
        )
    return matches[0]


def locate_run_files(run_dir: Path) -> RunFiles:
    # Resolve required output file paths for a run directory.
    results_csv = None
    try:
        results_csv = _find_single_file(run_dir, ["*__results.csv", "results.csv"])
    except FileNotFoundError:
        results_csv = None
    internal_boundary_csv = None
    try:
        internal_boundary_csv = _find_single_file(
            run_dir,
            [
                "*__results_heat_balance_internal_boundary.csv",
                "results_heat_balance_internal_boundary.csv",
            ],
        )
    except FileNotFoundError:
        internal_boundary_csv = None
    external_boundary_csv = None
    try:
        external_boundary_csv = _find_single_file(
            run_dir,
            [
                "*__results_heat_balance_external_boundary.csv",
                "results_heat_balance_external_boundary.csv",
            ],
        )
    except FileNotFoundError:
        external_boundary_csv = None
    return RunFiles(
        results_csv=results_csv,
        heat_balance_air_node_csv=_find_single_file(
            run_dir,
            [
                "*__results_heat_balance_air_node.csv",
                "results_heat_balance_air_node.csv",
            ],
        ),
        heat_balance_external_boundary_csv=external_boundary_csv,
        heat_balance_internal_boundary_csv=internal_boundary_csv,
        results_static_csv=_find_single_file(
            run_dir, ["*__results_static.csv", "results_static.csv"]
        ),
    )


def parse_numeric_rows(rows: list[list[str]]) -> list[list[float | None]]:
    parsed_rows: list[list[float | None]] = []
    for row in rows:
        parsed_row: list[float | None] = []
        for cell in row:
            if cell == "":
                parsed_row.append(None)
                continue
            try:
                parsed_row.append(float(cell))
            except ValueError:
                parsed_row.append(None)
        parsed_rows.append(parsed_row)
    return parsed_rows


def read_csv_timeseries(path: Path, has_units: bool = True) -> Timeseries:
    # Read a CSV file into a Timeseries.
    # When has_units is True, expects header + units rows.
    with path.open(newline="") as handle:
        reader = csv.reader(handle)
        rows = list(reader)
    if has_units and len(rows) < 2:
        raise ValueError(f"Expected header + units rows in {path}")
    if not has_units and len(rows) < 1:
        raise ValueError(f"Expected header row in {path}")
    header = rows[0]
    units = rows[1] if has_units else []
    data_rows = rows[2:] if has_units else rows[1:]
    return Timeseries(columns=header, units=units, rows=parse_numeric_rows(data_rows))


_MEASURED_HTC_KNOWN_KEYS = {
    "schema_version",
    "htc_value_w_per_k",
    "uncertainty_w_per_k",
    "window",
    "method_profile",
    "conditions",
    "source",
    "notes",
}

_METHOD_PROFILE_KNOWN_KEYS = {
    "method_family",
    "heat_input_proxy",
    "temperature_definition",
    "storage_handling",
    "ventilation_treatment",
    "windowing",
    "filters",
}

_WINDOW_INDEX_KNOWN_KEYS = {"type", "start_idx", "end_idx", "timestep_hours"}
_WINDOW_DATE_KNOWN_KEYS = {"type", "start", "end"}
_CONDITIONS_KNOWN_KEYS = {"occupancy_mode", "notes"}

_VALID_OCCUPANCY_MODES = {"unoccupied_coheating", "occupied_normal_use", "other"}


def _check_unknown_keys(
    obj: dict,
    known: set[str],
    prefix: str,
    errors: list[str],
) -> None:
    extra = set(obj.keys()) - known
    if extra:
        errors.append(f"{prefix} unknown keys: {sorted(extra)}")


def validate_measured_htc_payload(payload: dict) -> list[str]:
    """Validate a measured HTC payload dict. Returns a list of error strings (empty = valid)."""
    errors: list[str] = []

    _check_unknown_keys(payload, _MEASURED_HTC_KNOWN_KEYS, "Top-level", errors)

    # --- schema_version (required, const "0.1") ---
    if "schema_version" not in payload:
        errors.append("Missing required key: schema_version")
    elif payload["schema_version"] != "0.1":
        errors.append(
            f"Unsupported schema_version: {payload['schema_version']!r} (expected '0.1')"
        )

    # --- htc_value_w_per_k (required, number > 0) ---
    if "htc_value_w_per_k" not in payload:
        errors.append("Missing required key: htc_value_w_per_k")
    else:
        v = payload["htc_value_w_per_k"]
        if not isinstance(v, (int, float)):
            errors.append(f"htc_value_w_per_k must be a number, got {type(v).__name__}")
        elif v <= 0:
            errors.append(f"htc_value_w_per_k must be > 0, got {v}")

    # --- uncertainty_w_per_k (number >= 0 | null) ---
    unc = payload.get("uncertainty_w_per_k")
    if unc is not None:
        if not isinstance(unc, (int, float)):
            errors.append(
                f"uncertainty_w_per_k must be a number or null, got {type(unc).__name__}"
            )
        elif unc < 0:
            errors.append(f"uncertainty_w_per_k must be >= 0, got {unc}")

    # --- window (object | null) ---
    window = payload.get("window")
    if window is not None:
        if not isinstance(window, dict):
            errors.append(
                f"window must be an object or null, got {type(window).__name__}"
            )
        else:
            wtype = window.get("type")
            if wtype == "index":
                _check_unknown_keys(
                    window, _WINDOW_INDEX_KNOWN_KEYS, "window (type=index)", errors
                )
                for k in ("start_idx", "end_idx", "timestep_hours"):
                    if k not in window:
                        errors.append(f"window (type=index) missing key: {k}")
                if isinstance(window.get("start_idx"), int) and window["start_idx"] < 0:
                    errors.append(
                        f"window.start_idx must be >= 0, got {window['start_idx']}"
                    )
                if isinstance(window.get("end_idx"), int) and window["end_idx"] < 1:
                    errors.append(
                        f"window.end_idx must be >= 1, got {window['end_idx']}"
                    )
                th = window.get("timestep_hours")
                if isinstance(th, (int, float)) and th <= 0:
                    errors.append(f"window.timestep_hours must be > 0, got {th}")
            elif wtype == "date_range":
                _check_unknown_keys(
                    window, _WINDOW_DATE_KNOWN_KEYS, "window (type=date_range)", errors
                )
                for k in ("start", "end"):
                    if k not in window:
                        errors.append(f"window (type=date_range) missing key: {k}")
                    elif not isinstance(window[k], str):
                        errors.append(
                            f"window.{k} must be a string, got {type(window[k]).__name__}"
                        )
            elif wtype is None:
                errors.append("window object missing 'type' key")
            else:
                errors.append(
                    f"window.type must be 'index' or 'date_range', got {wtype!r}"
                )

    # --- method_profile (object | null) ---
    mp = payload.get("method_profile")
    if mp is not None:
        if not isinstance(mp, dict):
            errors.append(
                f"method_profile must be an object or null, got {type(mp).__name__}"
            )
        else:
            _check_unknown_keys(
                mp, _METHOD_PROFILE_KNOWN_KEYS, "method_profile", errors
            )
            missing = [k for k in _METHOD_PROFILE_KNOWN_KEYS if k not in mp]
            if missing:
                errors.append(f"method_profile missing keys: {sorted(missing)}")
            for k in _METHOD_PROFILE_KNOWN_KEYS:
                val = mp.get(k)
                if val is not None and not isinstance(val, str):
                    errors.append(
                        f"method_profile.{k} must be a string, got {type(val).__name__}"
                    )

    # --- conditions (object | null) ---
    cond = payload.get("conditions")
    if cond is not None:
        if not isinstance(cond, dict):
            errors.append(
                f"conditions must be an object or null, got {type(cond).__name__}"
            )
        else:
            _check_unknown_keys(cond, _CONDITIONS_KNOWN_KEYS, "conditions", errors)
            if "occupancy_mode" not in cond:
                errors.append("conditions missing required key: occupancy_mode")
            elif cond["occupancy_mode"] not in _VALID_OCCUPANCY_MODES:
                errors.append(
                    f"conditions.occupancy_mode must be one of {sorted(_VALID_OCCUPANCY_MODES)}, "
                    f"got {cond['occupancy_mode']!r}"
                )
            cnotes = cond.get("notes")
            if cnotes is not None and not isinstance(cnotes, str):
                errors.append(
                    f"conditions.notes must be a string or null, got {type(cnotes).__name__}"
                )

    # --- source (string | null) ---
    src = payload.get("source")
    if src is not None and not isinstance(src, str):
        errors.append(f"source must be a string or null, got {type(src).__name__}")

    # --- notes (string | null) ---
    notes = payload.get("notes")
    if notes is not None and not isinstance(notes, str):
        errors.append(f"notes must be a string or null, got {type(notes).__name__}")

    return errors


def read_measured_htc_json(path: Path) -> MeasuredHTC:
    """Read and validate a measured HTC JSON file.

    See schemas/measured_htc.schema.json for the formal specification.
    """
    payload = json.loads(path.read_text())
    errors = validate_measured_htc_payload(payload)
    if errors:
        raise ValueError(
            f"Invalid measured HTC file {path.name}:\n  " + "\n  ".join(errors)
        )

    htc_value = float(payload["htc_value_w_per_k"])

    method_profile = None
    mp = payload.get("method_profile")
    if mp is not None:
        method_profile = MethodProfile(
            method_family=mp["method_family"],
            heat_input_proxy=mp["heat_input_proxy"],
            temperature_definition=mp["temperature_definition"],
            storage_handling=mp["storage_handling"],
            ventilation_treatment=mp["ventilation_treatment"],
            windowing=mp["windowing"],
            filters=mp["filters"],
        )

    conditions = None
    cond_raw = payload.get("conditions")
    if cond_raw is not None:
        conditions = MeasuredConditions(
            occupancy_mode=cond_raw["occupancy_mode"],
            notes=cond_raw.get("notes"),
        )

    unc = payload.get("uncertainty_w_per_k")

    return MeasuredHTC(
        htc_value_w_per_k=htc_value,
        method_profile=method_profile,
        window=payload.get("window"),
        uncertainty_w_per_k=float(unc) if unc is not None else None,
        conditions=conditions,
        source=payload.get("source"),
        notes=payload.get("notes"),
    )


def read_results_static(path: Path) -> dict[str, float]:
    # Read results_static.csv into a simple label->value mapping.
    values: dict[str, float] = {}
    with path.open(newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) < 3:
                continue
            label = row[0].strip()
            value_raw = row[2].strip()
            if not label or not value_raw:
                continue
            try:
                values[label] = float(value_raw)
            except ValueError:
                continue
    return values


def read_external_conditions_file(path: Path) -> dict[str, Any]:
    # Read an ExternalConditions parameter file.
    #
    # Accepts either:
    # - a wrapper object containing {"ExternalConditions": {...}} (batch-runner style)
    # - a direct ExternalConditions object {...}
    #
    # Returns the ExternalConditions object as a dict.
    payload = json.loads(path.read_text())
    if isinstance(payload, dict) and "ExternalConditions" in payload:
        value = payload["ExternalConditions"]
    else:
        value = payload
    if not isinstance(value, dict):
        raise ValueError("ExternalConditions file must contain an object")
    return value


def merge_external_conditions_into_model(
    *,
    model: dict[str, Any],
    external_conditions: dict[str, Any],
    preserve_shading_segments: bool = True,
) -> dict[str, Any]:
    # Merge ExternalConditions into a model dict, preserving shading_segments.
    #
    # Mirrors Vulcan (rust-hem-runner) batch merge behavior:
    # - replace model["ExternalConditions"] with provided object
    # - if base model had ExternalConditions.shading_segments, restore it
    existing_shading_segments = None
    if preserve_shading_segments:
        existing_shading_segments = (
            model.get("ExternalConditions", {})
            if isinstance(model.get("ExternalConditions"), dict)
            else {}
        )
        if isinstance(existing_shading_segments, dict):
            existing_shading_segments = existing_shading_segments.get(
                "shading_segments"
            )
        else:
            existing_shading_segments = None

    model["ExternalConditions"] = external_conditions
    if preserve_shading_segments and existing_shading_segments is not None:
        ec = model.get("ExternalConditions")
        if isinstance(ec, dict):
            ec["shading_segments"] = existing_shading_segments
            model["ExternalConditions"] = ec
    return model


def offset_external_conditions_air_temperatures(
    external_conditions: dict[str, Any], delta_c: float
) -> dict[str, Any]:
    # Return a copy of ExternalConditions with air_temperatures offset by delta_c.
    copied = json.loads(
        json.dumps(external_conditions)
    )  # cheap deep copy for JSON-like dicts
    temps = copied.get("air_temperatures")
    if temps is None:
        raise ValueError("ExternalConditions missing required field: air_temperatures")
    if not isinstance(temps, list):
        raise ValueError("ExternalConditions.air_temperatures must be an array")
    shifted: list[float] = []
    for v in temps:
        try:
            shifted.append(float(v) + delta_c)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "ExternalConditions.air_temperatures must be numeric"
            ) from exc
    copied["air_temperatures"] = shifted
    return copied


def normalise_external_conditions(
    external_conditions: dict[str, Any],
    *,
    flatten_solar: bool = False,
    remove_solar: bool = False,
    flatten_wind: bool = False,
    remove_wind: bool = False,
) -> dict[str, Any]:
    """Return a copy of ExternalConditions with solar/wind normalised.

    Modes (solar):
      flatten_solar: replace hourly solar irradiance with the annual mean.
      remove_solar: set all solar irradiance to zero.
    Modes (wind):
      flatten_wind: replace hourly wind speed with the annual mean.
      remove_wind: set all wind speeds to zero.

    flatten and remove are mutually exclusive for each variable.
    """
    if flatten_solar and remove_solar:
        raise ValueError("flatten_solar and remove_solar are mutually exclusive")
    if flatten_wind and remove_wind:
        raise ValueError("flatten_wind and remove_wind are mutually exclusive")

    copied = json.loads(json.dumps(external_conditions))

    # --- Solar ---
    solar_keys = ("diffuse_horizontal_radiation", "direct_beam_radiation")
    if flatten_solar:
        for key in solar_keys:
            values = copied.get(key)
            if isinstance(values, list) and values:
                mean_val = sum(float(v) for v in values) / len(values)
                copied[key] = [mean_val] * len(values)
    elif remove_solar:
        for key in solar_keys:
            values = copied.get(key)
            if isinstance(values, list):
                copied[key] = [0.0] * len(values)

    # --- Wind ---
    if flatten_wind:
        speeds = copied.get("wind_speeds")
        if isinstance(speeds, list) and speeds:
            mean_speed = sum(float(v) for v in speeds) / len(speeds)
            copied["wind_speeds"] = [mean_speed] * len(speeds)
    elif remove_wind:
        speeds = copied.get("wind_speeds")
        if isinstance(speeds, list):
            copied["wind_speeds"] = [0.0] * len(speeds)

    return copied


@dataclass(frozen=True)
class ShoeboxLevers:
    """The dwelling attributes the browser sandbox exposes.

    `None` leaves the model's authored value alone, so a caller can move one
    attribute without restating the rest.

    Ranges these were validated over, by running each end through the engine:
    floor area 28.5-213.6 m2, wall U 0.10-1.50, roof U 0.11-2.30, glazing U
    0.8-4.8 W/m2K, glazing area 1.8-21.3 m2, airtightness 1-20 m3/(h.m2) at
    50 Pa, thermal bridging 0-52 W/K.
    """

    floor_area_m2: float | None = None
    wall_u_value: float | None = None
    roof_u_value: float | None = None
    glazing_u_value: float | None = None
    glazing_area_m2: float | None = None
    airtightness_q50: float | None = None
    thermal_bridging_w_per_k: float | None = None


def _is_external_wall(element: dict[str, Any]) -> bool:
    return (
        element.get("type") == "BuildingElementOpaque"
        and element.get("pitch") == 90
        and not element.get("is_external_door")
        and not element.get("is_unheated_pitched_roof")
    )


def _is_roof(element: dict[str, Any]) -> bool:
    return (
        element.get("type") == "BuildingElementOpaque"
        and not element.get("is_external_door")
        and (bool(element.get("is_unheated_pitched_roof")) or element.get("pitch") == 0)
    )


def _is_glazing(element: dict[str, Any]) -> bool:
    return element.get("type") == "BuildingElementTransparent"


def _glazed_area(elements: dict[str, Any]) -> float:
    total = 0.0
    for element in elements.values():
        if not isinstance(element, dict) or not _is_glazing(element):
            continue
        width = element.get("width")
        height = element.get("height")
        if isinstance(width, (int, float)) and isinstance(height, (int, float)):
            total += float(width) * float(height)
    return total


def _scale_dwelling_size(model: dict[str, Any], k: float) -> None:
    """Scale the dwelling's plan area by `k`, in place.

    Size is not one field. Plan areas scale as `k`; perimeters and wall runs
    scale as `sqrt(k)`, because a footprint growing in both directions gains
    area faster than it gains perimeter. Getting those powers wrong produces a
    dwelling whose walls and floor disagree about how big it is.
    """
    root = math.sqrt(k)

    for key in ("GroundFloorArea",):
        if isinstance(model.get(key), (int, float)):
            model[key] = float(model[key]) * k
    for key in ("BuildingLength", "BuildingWidth"):
        if isinstance(model.get(key), (int, float)):
            model[key] = float(model[key]) * root

    for zone in (model.get("Zone") or {}).values():
        if not isinstance(zone, dict):
            continue
        for key in ("volume", "livingroom_area", "restofdwelling_area"):
            if isinstance(zone.get(key), (int, float)):
                zone[key] = float(zone[key]) * k
        # Thermal bridging is a length-driven total, so it follows the
        # perimeter rather than the floor area.
        if isinstance(zone.get("ThermalBridging"), (int, float)):
            zone["ThermalBridging"] = float(zone["ThermalBridging"]) * root

        for element in (zone.get("BuildingElement") or {}).values():
            if not isinstance(element, dict):
                continue
            if element.get("type") == "BuildingElementGround":
                for key in ("area", "total_area"):
                    if isinstance(element.get(key), (int, float)):
                        element[key] = float(element[key]) * k
                if isinstance(element.get("perimeter"), (int, float)):
                    element["perimeter"] = float(element["perimeter"]) * root
                continue
            if element.get("is_external_door"):
                # A door stays a door.
                continue
            # Roofs cover the plan, so they scale as area. Walls and glazing
            # gain width but not height.
            is_conditioned_horizontal = element.get(
                "type"
            ) == "BuildingElementAdjacentConditionedSpace" and element.get("pitch") in (
                0,
                180,
            )
            factor = k if _is_roof(element) or is_conditioned_horizontal else root
            if isinstance(element.get("area"), (int, float)):
                element["area"] = float(element["area"]) * factor
            if isinstance(element.get("width"), (int, float)):
                element["width"] = float(element["width"]) * root

    ventilation = model.get("InfiltrationVentilation")
    if isinstance(ventilation, dict):
        leaks = ventilation.get("Leaks")
        if isinstance(leaks, dict) and isinstance(leaks.get("env_area"), (int, float)):
            leaks["env_area"] = float(leaks["env_area"]) * k
        # Part F sets a *floor* on extract rate, driven by wet rooms, and the
        # authored model already sits at it: scaling ventilation down with the
        # dwelling fails FHS validation outright. Scale up only. Without this,
        # anything past roughly 1.5x fails the same check from the other side.
        vent_factor = max(k, 1.0)
        for system in (ventilation.get("MechanicalVentilation") or {}).values():
            if not isinstance(system, dict):
                continue
            for key in (
                "design_outdoor_air_flow_rate",
                "measured_air_flow_rate",
                "measured_fan_power",
            ):
                if isinstance(system.get(key), (int, float)):
                    system[key] = float(system[key]) * vent_factor


def apply_shoebox_levers_to_model(
    model: dict[str, Any], levers: ShoeboxLevers
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply the sandbox's dwelling attributes to a model.

    Distinct from `apply_fabric_scalar_to_model`, which is a calibration tool:
    that applies one global scalar across every `u_value` and `ThermalBridging`
    at once and cannot address a category. This sets named attributes, which is
    what a person moving a control expects.

    Order matters. Size scaling runs first and drags thermal bridging with it,
    so an explicit bridging value has to be applied afterwards or it would be
    silently rescaled.
    """
    copied = json.loads(json.dumps(model))
    applied: dict[str, Any] = {}

    if levers.floor_area_m2 is not None:
        current = copied.get("GroundFloorArea")
        if not isinstance(current, (int, float)) or current <= 0:
            raise ValueError(
                "Model has no positive GroundFloorArea to scale from; "
                "floor area cannot be set."
            )
        k = float(levers.floor_area_m2) / float(current)
        if k <= 0:
            raise ValueError("floor_area_m2 must be > 0")
        _scale_dwelling_size(copied, k)
        applied["floor_area_scale"] = k

    zones = copied.get("Zone")
    if not isinstance(zones, dict):
        raise ValueError("Model must contain a Zone object")

    counts = {"walls": 0, "roofs": 0, "glazing": 0}
    for zone in zones.values():
        if not isinstance(zone, dict):
            continue
        elements = zone.get("BuildingElement")
        if not isinstance(elements, dict):
            continue

        for element in elements.values():
            if not isinstance(element, dict):
                continue
            if levers.wall_u_value is not None and _is_external_wall(element):
                element["u_value"] = float(levers.wall_u_value)
                counts["walls"] += 1
            elif levers.roof_u_value is not None and _is_roof(element):
                element["u_value"] = float(levers.roof_u_value)
                counts["roofs"] += 1
            elif levers.glazing_u_value is not None and _is_glazing(element):
                element["u_value"] = float(levers.glazing_u_value)
                counts["glazing"] += 1

        if levers.glazing_area_m2 is not None:
            current_glazed = _glazed_area(elements)
            if current_glazed <= 0:
                raise ValueError(
                    "Model has no transparent elements with width and height; "
                    "glazing area cannot be set."
                )
            # Widen the openings rather than adding new ones: the model's
            # window count and orientations are part of its geometry, and
            # inventing windows would change more than the control claims to.
            #
            # Width only. Scaling height too would be the natural way to hit an
            # area target, but `base_height`, `mid_height` and
            # `free_area_height` are all stated separately and would fall out of
            # step with it — and at the top of the range a ground-floor window
            # would extend through the ceiling.
            ratio = float(levers.glazing_area_m2) / current_glazed
            for element in elements.values():
                if not isinstance(element, dict) or not _is_glazing(element):
                    continue
                if isinstance(element.get("width"), (int, float)):
                    element["width"] = float(element["width"]) * ratio
            applied["glazing_width_scale"] = ratio

        # After size scaling, so an explicit value is not rescaled by it.
        if levers.thermal_bridging_w_per_k is not None and isinstance(
            zone.get("ThermalBridging"), (int, float)
        ):
            zone["ThermalBridging"] = float(levers.thermal_bridging_w_per_k)
            applied["thermal_bridging_w_per_k"] = float(levers.thermal_bridging_w_per_k)

    if levers.airtightness_q50 is not None:
        ventilation = copied.get("InfiltrationVentilation")
        leaks = ventilation.get("Leaks") if isinstance(ventilation, dict) else None
        if not isinstance(leaks, dict):
            raise ValueError(
                "Model has no InfiltrationVentilation.Leaks; airtightness "
                "cannot be set."
            )
        leaks["test_result"] = float(levers.airtightness_q50)
        applied["airtightness_q50"] = float(levers.airtightness_q50)

    applied["elements_set"] = counts
    return copied, applied


def apply_fixed_instant_electric_heating_to_preprocessed_model(
    model: dict[str, Any],
    rated_power_kw_per_zone: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Swap the heat emitters in a preprocessed model for fixed electric ones.

    Same intent as `apply_fixed_instant_electric_heating_to_model` — give every
    zone a heater big enough to hold setpoint, so the response to a temperature
    offset is the fabric's and not the heating system's — but applied after FHS
    preprocessing rather than before, and emitting the *core* schema's shape.

    It has to be two functions, because the two schemas disagree about what an
    `InstantElecHeater` is and no single object satisfies both:

    - the FHS input schema requires `convective_type` and, with
      `unevaluatedProperties: false`, rejects `frac_convective`
    - the core schema requires `frac_convective` and `Control`, and with
      `additionalProperties: false` rejects `convective_type`

    So substituting before the wrapper runs produces a model the wrapper
    refuses. Doing it here instead means the wrapper preprocesses the dwelling
    as authored — which is what makes its FHS assumptions meaningful — and only
    the model the core engine runs carries the substitution.

    Each replacement keeps the `Control` its system already referenced. That
    control is the one `flatten_fhs_heating_schedule_to_model` has just held at
    setpoint, and `Control` is required by the core schema: inventing a new one
    here would either fail validation or quietly restore a schedule.
    """
    if rated_power_kw_per_zone <= 0:
        raise ValueError("rated_power_kw_per_zone must be > 0")

    copied = json.loads(json.dumps(model))
    systems = copied.get("SpaceHeatSystem")
    if not isinstance(systems, dict) or not systems:
        return copied, {"systems_replaced": 0, "systems_without_control": []}

    energy_supply = copied.get("EnergySupply")
    supply_name = "mains elec"
    if isinstance(energy_supply, dict):
        for name, payload in energy_supply.items():
            if not isinstance(payload, dict):
                continue
            fuel = payload.get("fuel")
            if (
                isinstance(fuel, str)
                and fuel.lower().replace("_", " ") == "electricity"
            ):
                supply_name = name
                break
        else:
            energy_supply.setdefault(supply_name, {"fuel": "electricity"})
    else:
        copied["EnergySupply"] = {supply_name: {"fuel": "electricity"}}

    replaced = 0
    without_control: list[str] = []
    for name, payload in systems.items():
        if not isinstance(payload, dict):
            continue
        control = payload.get("Control")
        if not isinstance(control, str):
            # Nothing to hold it at setpoint, so replacing it would give the
            # zone an uncontrolled heater. Left alone and reported.
            without_control.append(name)
            continue
        systems[name] = {
            "type": "InstantElecHeater",
            "rated_power": rated_power_kw_per_zone,
            "frac_convective": 1.0,
            "EnergySupply": supply_name,
            "Control": control,
        }
        replaced += 1

    return copied, {
        "systems_replaced": replaced,
        "rated_power_kw_per_zone": rated_power_kw_per_zone,
        "energy_supply_name": supply_name,
        "systems_without_control": without_control,
    }


def apply_fixed_instant_electric_heating_to_model(
    model: dict[str, Any],
    rated_power_kw_per_zone: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Replace zone heating systems with fixed-size InstantElecHeater systems.

    Applied to the model *before* it reaches the engine, so whichever binary
    runs is the one that validates it.

    It writes both `frac_convective` and `convective_type`, which no current
    schema accepts together: the FHS input schema rejects `frac_convective`
    under `unevaluatedProperties: false`, and the core schema rejects
    `convective_type` under `additionalProperties: false`. Left as it is here
    rather than guessed at, because which shape is right depends on the route
    and neither branch is exercised against a real engine by anything in this
    repository.

    The preprocess route does not use this. It substitutes after preprocessing
    instead, where only the core schema applies and the shape is unambiguous —
    see `apply_fixed_instant_electric_heating_to_preprocessed_model`.
    """
    if rated_power_kw_per_zone <= 0:
        raise ValueError("rated_power_kw_per_zone must be > 0")

    copied = json.loads(json.dumps(model))
    zones = copied.get("Zone")
    if not isinstance(zones, dict):
        raise ValueError("Model must contain a Zone object")

    energy_supply = copied.setdefault("EnergySupply", {})
    if not isinstance(energy_supply, dict):
        raise ValueError("Model EnergySupply must be an object")

    energy_supply_name = None
    if "mains elec" in energy_supply and isinstance(energy_supply["mains elec"], dict):
        energy_supply_name = "mains elec"
    else:
        for name, payload in energy_supply.items():
            if not isinstance(payload, dict):
                continue
            fuel = payload.get("fuel")
            if (
                isinstance(fuel, str)
                and fuel.lower().replace("_", " ") == "electricity"
            ):
                energy_supply_name = name
                break

    energy_supply_created = False
    if energy_supply_name is None:
        energy_supply_name = "mains elec"
        energy_supply[energy_supply_name] = {"fuel": "electricity"}
        energy_supply_created = True

    copied["SpaceHeatSystem"] = {}
    replaced_count = 0
    for zone_name, zone_payload in zones.items():
        if not isinstance(zone_payload, dict):
            continue
        heating_name = f"{zone_name}_fixed_electric_heating"
        zone_payload["SpaceHeatSystem"] = heating_name
        copied["SpaceHeatSystem"][heating_name] = {
            "type": "InstantElecHeater",
            "rated_power": rated_power_kw_per_zone,
            "frac_convective": 1.0,
            "convective_type": "Air heating (convectors, fan coils etc.)",
            "EnergySupply": energy_supply_name,
        }
        replaced_count += 1

    return copied, {
        "zones_replaced": replaced_count,
        "rated_power_kw_per_zone": rated_power_kw_per_zone,
        "energy_supply_name": energy_supply_name,
        "energy_supply_created": energy_supply_created,
    }


def flatten_fhs_heating_schedule_to_model(
    model: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Hold every space-heating control at its setpoint for the whole run.

    This is the Python equivalent of the engine patch
    `fhs/0001-disable-heating-schedule`, applied to a model the FHS wrapper has
    already preprocessed. The patch replaces `create_heating_pattern` with a
    variant that writes `Some(setpoint)` into all 48 half-hourly periods; this
    does the same thing to the `SetpointTimeControl` that the stock
    `create_heating_pattern` produced.

    Why it exists at all: `h3 run` disables the FHS heating schedule by
    default, because the schedule's setbacks reduce heating-active timesteps
    and make the HTC estimate noisier. The patch route needs an environment
    variable, which does not cross into a `wasm32-unknown-unknown` build, and
    the wrapper submodule cannot be patched because the WASM build requires it
    pristine. This route needs neither.

    The setpoint is recovered as the schedule's highest value rather than by
    re-deriving `calc_zone_setpoint_fhs`: setbacks are lower and "off" is
    `null`, so the maximum is the setpoint, and reading it off the schedule
    cannot drift from whatever the wrapper actually applied.

    Only controls a `SpaceHeatSystem` actually references are touched. The
    preprocessed model also carries hot-water, window-opening and blind
    controls, and flattening those would change the run in ways that have
    nothing to do with the heating schedule.
    """
    copied = json.loads(json.dumps(model))
    controls = copied.get("Control")
    systems = copied.get("SpaceHeatSystem")
    if not isinstance(controls, dict) or not isinstance(systems, dict):
        return copied, {"controls_flattened": 0, "setpoints_c": []}

    referenced = {
        payload.get("Control")
        for payload in systems.values()
        if isinstance(payload, dict) and isinstance(payload.get("Control"), str)
    }

    flattened = 0
    setpoints: list[float] = []
    for name, control in controls.items():
        if name not in referenced or not isinstance(control, dict):
            continue
        if control.get("type") != "SetpointTimeControl":
            continue
        schedule = control.get("schedule")
        if not isinstance(schedule, dict):
            continue

        setpoint: float | None = None
        for day in schedule.values():
            if not isinstance(day, list):
                continue
            for value in day:
                if isinstance(value, (int, float)) and (
                    setpoint is None or value > setpoint
                ):
                    setpoint = float(value)
        if setpoint is None:
            continue

        for key, day in schedule.items():
            # `main` and `week` hold references to other schedule entries, not
            # setpoints; only the day profiles are numeric.
            if isinstance(day, list) and all(
                value is None or isinstance(value, (int, float)) for value in day
            ):
                schedule[key] = [setpoint] * len(day)

        # Advanced start pre-heats ahead of an occupied period. With no
        # unoccupied period left there is nothing to start early for, and
        # leaving it in would be a second difference from the patch, which
        # writes its control without one.
        control.pop("advanced_start", None)

        flattened += 1
        setpoints.append(setpoint)

    return copied, {"controls_flattened": flattened, "setpoints_c": setpoints}


def adapt_preprocessed_model_for_core(
    model: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int]]:
    """Make an FHS-preprocessed model acceptable to the core engine.

    `<key>__preproc.json` is the wrapper's finalized `Input`, not an authorable
    core-schema document, so the core entry point's schema check rejects it.

    Exactly one field is responsible: the wrapper resolves
    `areal_heat_capacity` onto every building element, including transparent
    ones, and `BuildingElementTransparent` in the core schema sets
    `additionalProperties: false` without listing it. Removing it is lossless —
    the core engine does not read areal heat capacity for a transparent
    element, and a run of the stripped model reports the same design HTC as the
    FHS run of the same input.

    Verified against the real WASM artifact: with this field removed and
    nothing else changed, the core engine runs the preprocessed model and
    produces every CSV h3 reads.
    """
    copied = json.loads(json.dumps(model))
    removed = 0
    zones = copied.get("Zone")
    if isinstance(zones, dict):
        for zone in zones.values():
            if not isinstance(zone, dict):
                continue
            elements = zone.get("BuildingElement")
            if not isinstance(elements, dict):
                continue
            for element in elements.values():
                if (
                    isinstance(element, dict)
                    and element.get("type") == "BuildingElementTransparent"
                    and element.pop("areal_heat_capacity", None) is not None
                ):
                    removed += 1
    return copied, {"transparent_areal_heat_capacity_removed": removed}


def set_simulation_period_on_model(
    model: dict[str, Any],
    *,
    start_hour: float,
    end_hour: float,
    step_hours: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Shorten (or lengthen) the period a preprocessed model simulates.

    Hours are counted from the start of the year, matching HEM's own
    `SimulationTime`. Jan-Mar is `start_hour=0, end_hour=2160`.

    This has to run *after* FHS preprocessing, not before. The authored model
    carries no `SimulationTime` at all; the wrapper writes `{0, 8760, 0.5}`
    while resolving the weather year and the occupancy and hot-water schedules
    against it. Shortening the model handed *to* the wrapper would either be
    overwritten or change what it derives. Shortening the model the wrapper
    hands *back* only changes how much of the resolved year the core engine
    steps through, which is the intent.

    Design HTC is unaffected by the period — it is a fabric calculation, and a
    two-week run and a full year report the same figure. The period only buys
    time back on the empirical estimate, which integrates over the run.
    """
    if end_hour <= start_hour:
        raise ValueError(
            f"Simulation end ({end_hour}) must be after start ({start_hour})."
        )
    if step_hours <= 0:
        raise ValueError(f"Simulation step must be > 0, got {step_hours}.")

    copied = json.loads(json.dumps(model))
    previous = copied.get("SimulationTime")
    copied["SimulationTime"] = {
        "start": float(start_hour),
        "end": float(end_hour),
        "step": float(step_hours),
    }
    return copied, {
        "previous": previous if isinstance(previous, dict) else None,
        "applied": copied["SimulationTime"],
    }


# The transmittance field to scale, per thermal-bridge type.
_THERMAL_BRIDGE_FIELDS = {
    "ThermalBridgeLinear": "linear_thermal_transmittance",
    "ThermalBridgePoint": "heat_transfer_coeff",
}


def _is_thermal_bridge(value: Any) -> bool:
    # True if `value` is one bridge rather than a mapping of named bridges.
    #
    # Current HEM models tag the bridge with a `type` field; older ones wrapped
    # it under a key named for the type.
    if not isinstance(value, dict):
        return False
    if value.get("type") in _THERMAL_BRIDGE_FIELDS:
        return True
    # Only trust the key name once nothing inside looks like a tagged bridge:
    # a current-schema mapping may legitimately *name* a bridge after its type,
    # and reading that as the legacy shape would scale it and skip its siblings.
    if any(
        isinstance(entry, dict) and entry.get("type") in _THERMAL_BRIDGE_FIELDS
        for entry in value.values()
    ):
        return False
    return any(key in value for key in _THERMAL_BRIDGE_FIELDS)


def apply_fabric_scalar_to_model(
    model: dict[str, Any], fabric_scalar: float
) -> tuple[dict[str, Any], dict[str, int]]:
    # Apply fabric scalar to input model (Phase 0 rules).
    #
    # Scaling rules:
    # - ThermalBridging (zone-level): scale numbers and linear/point entries.
    # - Building elements: scale u_value AND divide thermal_resistance_construction
    #   (both are needed because HEM uses R_construction for the dynamic simulation
    #   and u_value for static/design calculations).
    # - Ground elements: scale u_value with a cap to keep r_vi > 0.
    #
    # Returns a copied model and counts of scaled fields.
    copied = json.loads(json.dumps(model))
    counts = {
        "thermal_bridging_scaled": 0,
        "building_elements_scaled": 0,
        "ground_u_value_scaled": 0,
        "ground_u_value_capped": 0,
    }

    def _scale_number(value: Any) -> Any:
        if isinstance(value, (int, float)):
            return value * fabric_scalar
        return value

    def _scale_inverse(value: Any) -> Any:
        if isinstance(value, (int, float)) and value != 0:
            return value / fabric_scalar
        return value

    def _scale_thermal_bridge(entry: Any) -> Any:
        # Scale one bridge, in either the tagged or the wrapped shape.
        if isinstance(entry, (int, float)):
            counts["thermal_bridging_scaled"] += 1
            return entry * fabric_scalar
        if not isinstance(entry, dict):
            return entry
        field = _THERMAL_BRIDGE_FIELDS.get(entry.get("type"))
        if field is not None and isinstance(entry.get(field), (int, float)):
            entry[field] = _scale_number(entry[field])
            counts["thermal_bridging_scaled"] += 1
            return entry
        for wrapper, wrapped_field in _THERMAL_BRIDGE_FIELDS.items():
            inner = entry.get(wrapper)
            if isinstance(inner, dict) and isinstance(
                inner.get(wrapped_field), (int, float)
            ):
                inner[wrapped_field] = _scale_number(inner[wrapped_field])
                counts["thermal_bridging_scaled"] += 1
                entry[wrapper] = inner
        return entry

    def _scale_thermal_bridging(value: Any) -> Any:
        # `ThermalBridging` holds either one bridge or, as in current HEM
        # models, a mapping of bridge name -> bridge.
        if isinstance(value, (int, float)) or _is_thermal_bridge(value):
            return _scale_thermal_bridge(value)
        if isinstance(value, dict):
            for name, entry in value.items():
                value[name] = _scale_thermal_bridge(entry)
        return value

    def _scale_building_element(value: dict[str, Any], element_type: str) -> None:
        if element_type == "BuildingElementGround":
            _scale_ground_element(value)
            return
        scaled = False
        if "u_value" in value and isinstance(value.get("u_value"), (int, float)):
            value["u_value"] = _scale_number(value.get("u_value"))
            scaled = True
        if "thermal_resistance_construction" in value and isinstance(
            value.get("thermal_resistance_construction"), (int, float)
        ):
            value["thermal_resistance_construction"] = _scale_inverse(
                value.get("thermal_resistance_construction")
            )
            scaled = True
        if scaled:
            counts["building_elements_scaled"] += 1

    def _scale_ground_element(value: dict[str, Any]) -> None:
        # Cap u_value to keep r_vi > 0 (matches the HEM validation constraint).
        if not isinstance(value.get("u_value"), (int, float)):
            return
        u_value = float(value.get("u_value"))
        scaled = u_value * fabric_scalar
        r_floor = value.get("thermal_resistance_floor_construction")
        if isinstance(r_floor, (int, float)):
            r_si = 0.17
            r_gr = 0.5 / 1.5
            eps = 1e-6
            u_max = 1.0 / (r_si + float(r_floor) + r_gr + eps)
            if scaled > u_max:
                scaled = u_max
                counts["ground_u_value_capped"] += 1
        value["u_value"] = scaled
        counts["ground_u_value_scaled"] += 1

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, val in obj.items():
                if key == "ThermalBridging":
                    obj[key] = _scale_thermal_bridging(val)
                    continue
                if isinstance(val, dict):
                    element_type = val.get("type")
                    if isinstance(element_type, str) and element_type.startswith(
                        "BuildingElement"
                    ):
                        _scale_building_element(val, element_type)
                _walk(val)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(copied)
    return copied, counts


def offset_epw_dry_bulb(input_path: Path, output_path: Path, delta_c: float) -> None:
    # Write a new EPW file with dry-bulb temperature offset by delta_c.
    lines = input_path.read_text().splitlines()
    if len(lines) < 9:
        raise ValueError("EPW file must include header and at least one data row")
    header = lines[:8]
    data_lines = lines[8:]
    output_lines: list[str] = list(header)
    for line in data_lines:
        if not line.strip():
            continue
        parts = line.split(",")
        if len(parts) < 7:
            raise ValueError("EPW data row has fewer than 7 columns")
        original = parts[6]
        try:
            value = float(original)
        except ValueError as exc:
            raise ValueError("Invalid dry-bulb temperature in EPW row") from exc
        decimals = 0
        if "." in original:
            decimals = len(original.split(".", 1)[1])
        new_value = value + delta_c
        if decimals > 0:
            parts[6] = f"{new_value:.{decimals}f}"
        else:
            parts[6] = f"{new_value:.3f}".rstrip("0").rstrip(".")
        output_lines.append(",".join(parts))
    output_path.write_text("\n".join(output_lines) + "\n")
