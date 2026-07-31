# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CalibrationData:
    """Calibration-specific data extracted from the ``calibration`` block."""

    htc_target_w_per_k: float
    htc_baseline_w_per_k: float
    htc_calibrated_w_per_k: float | None
    residual_w_per_k: float | None
    scalar: float
    s_global: float
    # Per-month HTC for each calibration stage (parallel lists).
    monthly_labels: list[str]
    monthly_baseline: list[float]
    monthly_global: list[float]
    monthly_calibrated: list[float]
    monthly_mask_reason: str
    # Masking settings used during calibration.
    masking_heating_eps_w: float
    masking_unmet_eps_kwh: float
    masking_valid_min_fraction: float
    masking_mask_block_hours: float | None
    # Calibration inputs.
    delta_scalar: float
    calibration_stat: str
    calibration_block: str
    lambda_reg: float
    scalar_bounds: tuple[float, float]
    htc_static_baseline_w_per_k: float | None = None
    htc_static_calibrated_w_per_k: float | None = None
    htc_dynamic_baseline_w_per_k: float | None = None
    htc_dynamic_calibrated_w_per_k: float | None = None
    htc_empirical_baseline_w_per_k: float | None = None
    htc_empirical_calibrated_w_per_k: float | None = None
    fee_baseline_kwh_per_m2_yr: float | None = None
    fee_calibrated_kwh_per_m2_yr: float | None = None
    fee_enabled: bool = False
    fee_status: str | None = None
    iterative_executed_steps: int = 0
    iterative_history: list[dict[str, float | int | None]] = field(default_factory=list)


@dataclass(frozen=True)
class SummarySeries:
    # Normalized, validated series extracted from htc_summary.json.
    path: Path
    offsets_k: list[float]
    timestep_hours: float
    htc_empirical: float | None
    htc_empirical_masked: float | None
    htc_design: float | None
    q_loss_by_offset: dict[str, list[float]]
    q_loss_components_by_offset: dict[str, dict[str, list[float]]]
    q_loss_by_offset_by_source: dict[str, dict[str, list[float]]]
    q_system_total_by_offset: dict[str, list[float]]
    q_system_fabric_int_by_offset: dict[str, list[float]]
    hem_htc_components_by_offset: dict[str, dict[str, list[float | None]]]
    base_results_series: dict[str, dict[str, object]]
    month_bins: list[dict[str, object]]
    air_node_energy_balance: dict[str, object]
    internal_air_temp_c: list[float]
    external_air_temp_c: list[float]
    wind_speed_ms: list[float]
    wind_direction_deg: list[float]
    htc_by_source: dict[str, list[float]]
    htc_mean_by_source: dict[str, float]
    htc_primary_source: str
    base_results_dir: Path | None
    ventilation: VentilationSeries | None
    calibration: CalibrationData | None = None
    # Job/engine metadata for the Assumptions table.
    mode: str = "run"
    engine_runner: str | None = None
    engine_commit: str | None = None
    env_overrides: dict[str, str] = field(default_factory=dict)
    extra_flags: list[str] = field(default_factory=list)
    analysis_window_type: str | None = None
    analysis_window_start: int | None = None
    analysis_window_end: int | None = None
    selected_indices: list[int] = field(default_factory=list)
    fihc_enabled: bool = False
    fixed_electric_heating: dict[str, object] | None = None


@dataclass(frozen=True)
class VentilationSeries:
    # Parsed ventilation_results.csv data (per-timestep, min/max rows).
    timestep: list[int]
    reporting_flag: list[str]
    ach: list[float]
    incoming_flow_m3h: list[float]
    total_volume_m3: list[float]
    qm_in: list[float]
    qm_in_window: list[float]
    qm_in_vents: list[float]
    qm_in_leaks: list[float]
    qm_in_comb: list[float]
    qm_in_passive: list[float]
    qm_in_mech: list[float]
    qm_out_mech: list[float]


@dataclass(frozen=True)
class PlotSettings:
    heating_eps_w: float = 0.0
    unmet_eps_kwh: float = 0.0
    valid_min_fraction: float | None = 0.95
    # Optional: gate the per-timestep mask at a coarser granularity.
    #
    # Example: 24h makes "keep/drop" decisions at the day level:
    # - compute the per-timestep mask (heating/unmet thresholds)
    # - keep a day only if mask-pass fraction >= valid_min_fraction
    # - within kept days, still only keep timesteps that pass the per-timestep mask
    mask_block_hours: float | None = 24.0
    agg_hours: tuple[float, ...] | None = None
