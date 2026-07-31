# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path

from .models import CalibrationData, SummarySeries, VentilationSeries


def _float_or_default(payload: dict[str, object], key: str, default: float) -> float:
    """Return a numeric field when present; otherwise use default."""
    value = payload.get(key)
    return float(value) if isinstance(value, (int, float)) else default


def _bounds_or_default(
    payload: dict[str, object], key: str, default: tuple[float, float]
) -> tuple[float, float]:
    """Return a two-item numeric bounds tuple when present; otherwise default."""
    value = payload.get(key)
    if (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    ):
        return float(value[0]), float(value[1])
    return default


def find_summary_jsons(paths: Iterable[Path], *, recursive: bool = True) -> list[Path]:
    # Resolve individual files and directories into a de-duplicated list of summary JSONs.
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            pattern = "**/htc_summary*.json" if recursive else "htc_summary*.json"
            files.extend(sorted(p.glob(pattern)))
        else:
            files.append(p)
    seen: set[Path] = set()
    out: list[Path] = []
    for f in files:
        rf = f.resolve()
        if rf in seen:
            continue
        seen.add(rf)
        out.append(f)
    return out


def load_summary_series(path: Path) -> SummarySeries:
    # Read and coerce a summary JSON into concrete lists/dicts of floats.
    data = json.loads(path.read_text())
    derived = data.get("derived") or {}
    summary = data.get("summary") or {}
    job = data.get("job") or {}
    runs = data.get("runs") or []
    window = job.get("window") or {}
    offsets = job.get("offsets_k") or []
    q_loss_by_offset = derived.get("q_loss_w_by_offset_k") or {}
    q_loss_components_by_offset = derived.get("q_loss_components_w_by_offset_k") or {}
    q_loss_by_offset_by_source = derived.get("q_loss_w_by_offset_k_by_source") or {}
    q_system_total_by_offset = derived.get("q_system_total_w_by_offset_k") or {}
    q_system_fabric_int_by_offset = (
        derived.get("q_system_fabric_int_heat_cool_w_by_offset_k") or {}
    )
    hem_htc_components_by_offset = (
        derived.get("hem_htc_components_w_per_k_by_offset_k") or {}
    )
    base_results_series = derived.get("base_results_series") or {}
    month_bins = derived.get("month_bins") or []
    air_node_energy_balance = derived.get("air_node_energy_balance") or {}
    temperature_series = derived.get("temperature_series") or {}
    internal_air_temp = None
    external_air_temp = None
    wind_speed = None
    wind_direction = None
    if isinstance(temperature_series, dict):
        internal_air_temp = temperature_series.get("internal_air_temp_c")
        external_air_temp = temperature_series.get("external_air_temp_c")
        wind_speed = temperature_series.get("wind_speed_ms")
        wind_direction = temperature_series.get("wind_direction_deg")
    htc_by_source = derived.get("htc_w_per_k_by_timestep_by_source") or {}
    selected_indices_raw = derived.get("selected_indices") or []
    htc_mean_by_source = summary.get("htc_empirical_by_source") or {}
    htc_primary_source = summary.get("htc_empirical_source_primary") or ""
    timestep_hours = _float_or_default(window, "timestep_hours", 0.5)
    htc_empirical = summary.get("htc_empirical_w_per_k")
    htc_empirical_masked = summary.get("htc_empirical_masked_w_per_k")
    htc_design = summary.get("htc_design_w_per_k")
    if not isinstance(q_loss_by_offset, dict) or not isinstance(
        q_loss_components_by_offset, dict
    ):
        raise ValueError(f"Invalid q_loss series in {path}")
    if not isinstance(q_loss_by_offset_by_source, dict):
        raise ValueError(f"Invalid q_loss_by_source series in {path}")
    if not isinstance(q_system_total_by_offset, dict):
        raise ValueError(f"Invalid q_system_total series in {path}")
    if not isinstance(q_system_fabric_int_by_offset, dict):
        raise ValueError(f"Invalid q_system_fabric_int series in {path}")
    if not isinstance(hem_htc_components_by_offset, dict):
        raise ValueError(f"Invalid hem_htc_components series in {path}")
    if not isinstance(base_results_series, dict):
        raise ValueError(f"Invalid base_results_series in {path}")
    if not isinstance(month_bins, list):
        raise ValueError(f"Invalid month_bins in {path}")
    if not isinstance(air_node_energy_balance, dict):
        raise ValueError(f"Invalid air_node_energy_balance in {path}")
    if not isinstance(htc_by_source, dict):
        raise ValueError(f"Invalid htc_w_per_k_by_timestep_by_source in {path}")
    if not isinstance(selected_indices_raw, list):
        raise ValueError(f"Invalid selected_indices in {path}")
    if not isinstance(htc_mean_by_source, dict):
        raise ValueError(f"Invalid htc_empirical_by_source in {path}")
    if internal_air_temp is not None and not isinstance(internal_air_temp, dict):
        raise ValueError(f"Invalid internal air temp series in {path}")
    if external_air_temp is not None and not isinstance(external_air_temp, list):
        raise ValueError(f"Invalid external air temp series in {path}")
    if wind_speed is not None and not isinstance(wind_speed, list):
        raise ValueError(f"Invalid wind speed series in {path}")
    if wind_direction is not None and not isinstance(wind_direction, list):
        raise ValueError(f"Invalid wind direction series in {path}")
    offsets_f = [float(x) for x in offsets if isinstance(x, (int, float))]
    base_results_dir = _base_results_dir_from_runs(runs)
    ventilation = (
        _load_ventilation_series(base_results_dir) if base_results_dir else None
    )
    calibration = _parse_calibration_block(data.get("calibration"))

    engine = job.get("engine") or {}
    manifest = engine.get("patched_manifest") or {}
    commit_full = (
        manifest.get("commit")
        if isinstance(manifest, dict) and isinstance(manifest.get("commit"), str)
        else ""
    )
    if not commit_full:
        raw_commit = engine.get("patched_manifest_commit")
        commit_full = raw_commit if isinstance(raw_commit, str) else ""
    engine_commit = commit_full[:7] if commit_full else None
    engine_runner = (
        engine.get("runner") if isinstance(engine.get("runner"), str) else None
    )
    raw_env = engine.get("env_overrides") or {}
    env_overrides = (
        {str(k): str(v) for k, v in raw_env.items()}
        if isinstance(raw_env, dict)
        else {}
    )
    raw_flags = engine.get("extra_flags") or []
    extra_flags = [str(f) for f in raw_flags] if isinstance(raw_flags, list) else []
    fixed_electric_heating = (
        job.get("fixed_electric_heating")
        if isinstance(job.get("fixed_electric_heating"), dict)
        else None
    )
    win_type = window.get("type")
    analysis_window_type = str(win_type) if isinstance(win_type, str) else None
    win_start = window.get("start_idx")
    analysis_window_start = (
        int(win_start) if isinstance(win_start, (int, float)) else None
    )
    win_end = window.get("end_idx")
    analysis_window_end = int(win_end) if isinstance(win_end, (int, float)) else None
    fihc_vals = (
        q_system_fabric_int_by_offset.values()
        if isinstance(q_system_fabric_int_by_offset, dict)
        else []
    )
    fihc_enabled = any(isinstance(v, list) and len(v) > 0 for v in fihc_vals)
    mode = "calibrate" if calibration is not None else "run"
    selected_indices = [
        int(v) for v in selected_indices_raw if isinstance(v, (int, float))
    ]

    return SummarySeries(
        path=path,
        offsets_k=offsets_f,
        timestep_hours=timestep_hours,
        htc_empirical=(
            float(htc_empirical) if isinstance(htc_empirical, (int, float)) else None
        ),
        htc_empirical_masked=(
            float(htc_empirical_masked)
            if isinstance(htc_empirical_masked, (int, float))
            else None
        ),
        htc_design=float(htc_design) if isinstance(htc_design, (int, float)) else None,
        q_loss_by_offset=_coerce_offset_series(q_loss_by_offset),
        q_loss_components_by_offset=_coerce_component_series(
            q_loss_components_by_offset
        ),
        q_loss_by_offset_by_source=_coerce_nested_offset_series(
            q_loss_by_offset_by_source
        ),
        q_system_total_by_offset=_coerce_offset_series(q_system_total_by_offset),
        q_system_fabric_int_by_offset=_coerce_offset_series(
            q_system_fabric_int_by_offset
        ),
        hem_htc_components_by_offset=_coerce_component_series_optional(
            hem_htc_components_by_offset
        ),
        base_results_series=_coerce_base_series(base_results_series),
        month_bins=_coerce_month_bins(month_bins),
        air_node_energy_balance=air_node_energy_balance,
        internal_air_temp_c=_coerce_temp_series(internal_air_temp),
        external_air_temp_c=(
            [float(v) for v in external_air_temp]
            if isinstance(external_air_temp, list)
            else []
        ),
        wind_speed_ms=(
            [float(v) for v in wind_speed] if isinstance(wind_speed, list) else []
        ),
        wind_direction_deg=(
            [float(v) for v in wind_direction]
            if isinstance(wind_direction, list)
            else []
        ),
        htc_by_source=_coerce_offset_series(htc_by_source),
        htc_mean_by_source={
            str(k): float(v)
            for k, v in htc_mean_by_source.items()
            if isinstance(v, (int, float))
        },
        htc_primary_source=str(htc_primary_source),
        base_results_dir=base_results_dir,
        ventilation=ventilation,
        calibration=calibration,
        mode=mode,
        engine_runner=engine_runner,
        engine_commit=engine_commit,
        env_overrides=env_overrides,
        extra_flags=extra_flags,
        analysis_window_type=analysis_window_type,
        analysis_window_start=analysis_window_start,
        analysis_window_end=analysis_window_end,
        selected_indices=selected_indices,
        fihc_enabled=fihc_enabled,
        fixed_electric_heating=fixed_electric_heating,
    )


def _parse_calibration_block(raw: object) -> CalibrationData | None:
    """Parse the optional ``calibration`` dict from a summary JSON."""
    if not isinstance(raw, dict):
        return None
    metrics = raw.get("metrics") or {}
    scalars = raw.get("scalars") or {}
    sensitivities = raw.get("sensitivities") or {}
    monthly = raw.get("monthly") or {}
    masking = raw.get("masking") or {}
    inputs = raw.get("inputs") or {}
    iterative = raw.get("iterative") or {}
    fee_metrics = raw.get("fee_metrics") or {}
    if not isinstance(metrics, dict) or not isinstance(scalars, dict):
        return None
    htc_target = metrics.get("htc_target_w_per_k")
    htc_baseline = metrics.get("htc_baseline_w_per_k")
    if not isinstance(htc_target, (int, float)) or not isinstance(
        htc_baseline, (int, float)
    ):
        return None
    htc_cal = metrics.get("htc_calibrated_w_per_k")
    residual = metrics.get("residual_w_per_k")
    htc_static_baseline = metrics.get("htc_static_baseline_w_per_k")
    htc_static_calibrated = metrics.get("htc_static_calibrated_w_per_k")
    htc_dynamic_baseline = metrics.get("htc_dynamic_baseline_w_per_k")
    htc_dynamic_calibrated = metrics.get("htc_dynamic_calibrated_w_per_k")
    htc_empirical_baseline = metrics.get("htc_empirical_baseline_w_per_k")
    htc_empirical_calibrated = metrics.get("htc_empirical_calibrated_w_per_k")
    scalar_bounds = _bounds_or_default(
        inputs, "scalar_bounds", _bounds_or_default(inputs, "vent_bounds", (0.1, 2.5))
    )
    calibration_stat_raw = str(inputs.get("calibration_stat") or "mean").lower()
    calibration_stat = (
        calibration_stat_raw if calibration_stat_raw in {"mean", "median"} else "mean"
    )
    delta_scalar_raw = inputs.get("delta_scalar")
    if isinstance(delta_scalar_raw, (int, float)):
        delta_scalar = float(delta_scalar_raw)
    else:
        # Backward compatibility for older summaries.
        delta_scalar = _float_or_default(inputs, "delta_vent", 0.2)

    scalar_raw = scalars.get("scalar")
    if isinstance(scalar_raw, (int, float)):
        scalar = float(scalar_raw)
    else:
        scalar = _float_or_default(scalars, "vent_scalar", 1.0)

    s_global_raw = sensitivities.get("s_global")
    if isinstance(s_global_raw, (int, float)):
        s_global = float(s_global_raw)
    else:
        s_global = _float_or_default(sensitivities, "s_vent", 0.0)

    iterative_executed_steps = 0
    iterative_history: list[dict[str, float | int | None]] = []
    if isinstance(iterative, dict):
        executed_raw = iterative.get("executed_steps")
        if isinstance(executed_raw, (int, float)):
            iterative_executed_steps = int(executed_raw)
        history_raw = iterative.get("history")
        if isinstance(history_raw, list):
            for entry in history_raw:
                if not isinstance(entry, dict):
                    continue

                def _num_or_none(key: str) -> float | None:
                    value = entry.get(key)
                    return float(value) if isinstance(value, (int, float)) else None

                step_raw = entry.get("step")
                step = int(step_raw) if isinstance(step_raw, (int, float)) else None
                iterative_history.append(
                    {
                        "step": step,
                        "scalar_before": _num_or_none("scalar_before"),
                        "scalar_after": _num_or_none("scalar_after"),
                        "delta_scalar_applied": _num_or_none("delta_scalar_applied"),
                        "htc_before_w_per_k": _num_or_none("htc_before_w_per_k"),
                        "htc_after_w_per_k": _num_or_none("htc_after_w_per_k"),
                        "residual_after_w_per_k": _num_or_none(
                            "residual_after_w_per_k"
                        ),
                        "sensitivity_s_global": _num_or_none("sensitivity_s_global"),
                    }
                )

    fee_baseline: float | None = None
    fee_calibrated: float | None = None
    fee_enabled = (
        bool(fee_metrics.get("enabled")) if isinstance(fee_metrics, dict) else False
    )
    fee_status = (
        str(fee_metrics.get("status"))
        if isinstance(fee_metrics, dict) and isinstance(fee_metrics.get("status"), str)
        else None
    )
    if isinstance(fee_metrics, dict) and fee_status == "ok":
        baseline_raw = fee_metrics.get("baseline_kwh_per_m2_yr")
        calibrated_raw = fee_metrics.get("calibrated_kwh_per_m2_yr")
        if isinstance(baseline_raw, (int, float)):
            fee_baseline = float(baseline_raw)
        if isinstance(calibrated_raw, (int, float)):
            fee_calibrated = float(calibrated_raw)

    return CalibrationData(
        htc_target_w_per_k=float(htc_target),
        htc_baseline_w_per_k=float(htc_baseline),
        htc_calibrated_w_per_k=(
            float(htc_cal) if isinstance(htc_cal, (int, float)) else None
        ),
        residual_w_per_k=(
            float(residual) if isinstance(residual, (int, float)) else None
        ),
        htc_static_baseline_w_per_k=(
            float(htc_static_baseline)
            if isinstance(htc_static_baseline, (int, float))
            else None
        ),
        htc_static_calibrated_w_per_k=(
            float(htc_static_calibrated)
            if isinstance(htc_static_calibrated, (int, float))
            else None
        ),
        htc_dynamic_baseline_w_per_k=(
            float(htc_dynamic_baseline)
            if isinstance(htc_dynamic_baseline, (int, float))
            else None
        ),
        htc_dynamic_calibrated_w_per_k=(
            float(htc_dynamic_calibrated)
            if isinstance(htc_dynamic_calibrated, (int, float))
            else None
        ),
        htc_empirical_baseline_w_per_k=(
            float(htc_empirical_baseline)
            if isinstance(htc_empirical_baseline, (int, float))
            else None
        ),
        htc_empirical_calibrated_w_per_k=(
            float(htc_empirical_calibrated)
            if isinstance(htc_empirical_calibrated, (int, float))
            else None
        ),
        scalar=scalar,
        s_global=s_global,
        monthly_labels=[str(v) for v in monthly.get("labels") or []],
        monthly_baseline=[
            float(v) for v in (monthly.get("baseline_htc_w_per_k") or [])
        ],
        monthly_global=[
            float(v)
            for v in (
                monthly.get("global_htc_w_per_k")
                or monthly.get("vent_htc_w_per_k")
                or monthly.get("fabric_htc_w_per_k")
                or []
            )
        ],
        monthly_calibrated=[
            float(v) for v in (monthly.get("calibrated_htc_w_per_k") or [])
        ],
        monthly_mask_reason=str(monthly.get("mask_reason") or ""),
        masking_heating_eps_w=_float_or_default(masking, "heating_eps_w", 0.0),
        masking_unmet_eps_kwh=_float_or_default(masking, "unmet_eps_kwh", 0.0),
        masking_valid_min_fraction=_float_or_default(
            masking, "valid_min_fraction", 0.95
        ),
        masking_mask_block_hours=(
            float(masking["mask_block_hours"])
            if masking.get("mask_block_hours") is not None
            else None
        ),
        delta_scalar=delta_scalar,
        calibration_stat=calibration_stat,
        calibration_block=str(inputs.get("calibration_block") or "monthly").lower(),
        lambda_reg=_float_or_default(inputs, "lambda_reg", 0.01),
        scalar_bounds=scalar_bounds,
        fee_baseline_kwh_per_m2_yr=fee_baseline,
        fee_calibrated_kwh_per_m2_yr=fee_calibrated,
        fee_enabled=fee_enabled,
        fee_status=fee_status,
        iterative_executed_steps=iterative_executed_steps,
        iterative_history=iterative_history,
    )


def _coerce_offset_series(payload: dict[str, object]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for k, v in payload.items():
        if isinstance(v, list):
            out[str(k)] = [float(x) for x in v]
    return out


def _coerce_component_series(
    payload: dict[str, object],
) -> dict[str, dict[str, list[float]]]:
    out: dict[str, dict[str, list[float]]] = {}
    for k, comp in payload.items():
        if not isinstance(comp, dict):
            continue
        comp_out: dict[str, list[float]] = {}
        for term, vv in comp.items():
            if isinstance(vv, list):
                comp_out[str(term)] = [float(x) for x in vv]
        out[str(k)] = comp_out
    return out


def _coerce_component_series_optional(
    payload: dict[str, object],
) -> dict[str, dict[str, list[float | None]]]:
    out: dict[str, dict[str, list[float | None]]] = {}
    for k, comp in payload.items():
        if not isinstance(comp, dict):
            continue
        comp_out: dict[str, list[float | None]] = {}
        for term, vv in comp.items():
            if isinstance(vv, list):
                comp_out[str(term)] = [
                    float(x) if isinstance(x, (int, float)) else None for x in vv
                ]
        if comp_out:
            out[str(k)] = comp_out
    return out


def _coerce_nested_offset_series(
    payload: dict[str, object],
) -> dict[str, dict[str, list[float]]]:
    out: dict[str, dict[str, list[float]]] = {}
    for source, series in payload.items():
        if not isinstance(series, dict):
            continue
        out[str(source)] = _coerce_offset_series(series)
    return out


def _coerce_base_series(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for key, series in payload.items():
        if not isinstance(series, dict):
            continue
        values = series.get("values")
        if not isinstance(values, list):
            continue
        out[str(key)] = {
            "column": series.get("column"),
            "unit": series.get("unit"),
            "values": [float(x) for x in values],
        }
    return out


def _coerce_temp_series(payload: object) -> list[float]:
    if not isinstance(payload, dict):
        return []
    values = payload.get("values")
    if not isinstance(values, list):
        return []
    return [float(x) for x in values]


def _coerce_month_bins(payload: list[object]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        start = item.get("start")
        end = item.get("end")
        label = item.get("label")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        out.append(
            {
                "start": start,
                "end": end,
                "label": str(label) if label is not None else "",
            }
        )
    return out


def _base_results_dir_from_runs(runs: object) -> Path | None:
    if not isinstance(runs, list):
        return None
    for run in runs:
        if not isinstance(run, dict):
            continue
        offset = run.get("offset_k")
        if isinstance(offset, (int, float)) and abs(float(offset)) < 1e-9:
            results_dir = run.get("results_dir")
            if isinstance(results_dir, str):
                return Path(results_dir)
    return None


def _load_ventilation_series(results_dir: Path) -> VentilationSeries | None:
    if not results_dir.exists():
        return None
    matches = sorted(results_dir.glob("*ventilation_results.csv"))
    if not matches:
        return None
    path = matches[0]
    timestep: list[int] = []
    reporting_flag: list[str] = []
    ach: list[float] = []
    incoming_flow_m3h: list[float] = []
    total_volume_m3: list[float] = []
    qm_in: list[float] = []
    qm_in_window: list[float] = []
    qm_in_vents: list[float] = []
    qm_in_leaks: list[float] = []
    qm_in_comb: list[float] = []
    qm_in_passive: list[float] = []
    qm_in_mech: list[float] = []
    qm_out_mech: list[float] = []

    with path.open(newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        units = next(reader, None)
        if header is None or units is None:
            return None
        for row in reader:
            if not row or len(row) < 22:
                continue
            try:
                timestep.append(int(float(row[0])))
            except (ValueError, TypeError):
                continue
            reporting_flag.append(str(row[1]))

            def _f(idx: int) -> float:
                try:
                    return float(row[idx])
                except (ValueError, TypeError):
                    return float("nan")

            incoming_flow_m3h.append(_f(3))
            total_volume_m3.append(_f(4))
            ach.append(_f(5))
            qm_in_window.append(_f(8))
            qm_in_vents.append(_f(10))
            qm_in_leaks.append(_f(12))
            qm_in_comb.append(_f(14))
            qm_in_passive.append(_f(16))
            qm_in_mech.append(_f(18))
            qm_out_mech.append(_f(19))
            qm_in.append(_f(21))
    return VentilationSeries(
        timestep=timestep,
        reporting_flag=reporting_flag,
        ach=ach,
        incoming_flow_m3h=incoming_flow_m3h,
        total_volume_m3=total_volume_m3,
        qm_in=qm_in,
        qm_in_window=qm_in_window,
        qm_in_vents=qm_in_vents,
        qm_in_leaks=qm_in_leaks,
        qm_in_comb=qm_in_comb,
        qm_in_passive=qm_in_passive,
        qm_in_mech=qm_in_mech,
        qm_out_mech=qm_out_mech,
    )
