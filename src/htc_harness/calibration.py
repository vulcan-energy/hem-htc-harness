# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
"""Fit a single fabric/ventilation scalar to a measured HTC target.

One scalar drives both the fabric transform and `HEM_ACH_CAL_FACTOR`, so
calibration does not separately identify fabric from ventilation. This module
used to carry `vent_` and `fabric_` twins of every variable, always assigned the
same value; naming the one scalar it actually solves for is the honest shape.
"""

from __future__ import annotations

from collections.abc import Callable

from .empirical import (
    solve_single_calibration_increment,
    solve_single_calibration_scalar,
)
from .reader import apply_fabric_scalar_to_model


def run_initial_calibration(
    *,
    label: str,
    delta_scalar: float,
    lambda_reg: float,
    scalar_bounds: tuple[float, float],
    heating_eps_w: float,
    unmet_eps_kwh: float,
    valid_min_fraction: float,
    mask_block_hours_eff: float | None,
    calibration_stat: str,
    extract_mask_fn: Callable[..., tuple[list[bool] | None, str]],
    run_step: Callable[..., tuple[dict, dict]],
) -> dict[str, object]:
    baseline_payload, baseline_summary = run_step(f"{label}_base")

    baseline_mask, mask_reason = extract_mask_fn(
        baseline_payload,
        heating_eps_w=heating_eps_w,
        unmet_eps_kwh=unmet_eps_kwh,
        mask_block_hours=mask_block_hours_eff,
        valid_min_fraction=valid_min_fraction,
    )

    htc_baseline = baseline_summary.get("masked_aggregate_w_per_k")
    measured_payload = baseline_payload.get("measured_htc") or {}
    htc_target = (
        measured_payload.get("htc_value_w_per_k")
        if isinstance(measured_payload, dict)
        else None
    )
    if not isinstance(htc_target, (int, float)):
        return {
            "failed_pre_solve": True,
            "diagnostic_payload": None,
            "error_message": "Measured HTC value is missing in measured_htc file",
        }

    if baseline_mask is None:
        # Checked before the probe below, which is a full extra HEM batch and
        # the most expensive step in a calibration run.
        #
        # The objective is the *masked* monthly aggregate, and iterative
        # refinement freezes this mask so the objective stays fixed across
        # steps. With no mask there is nothing stable to calibrate against —
        # and the aggregate would silently fall back to an unmasked value, so
        # the solve would optimise something other than what it reports.
        message = (
            "Calibration needs a per-timestep mask, and none could be built: "
            f"{mask_reason or 'reason unavailable'}"
        )
        diagnostic_payload = dict(baseline_payload)
        diagnostic_payload["calibration"] = {
            "status": "failed_pre_solve",
            "failure": {
                "reason": message,
                "missing_metrics": ["baseline timestep mask"],
            },
            "masking": {
                "heating_eps_w": heating_eps_w,
                "unmet_eps_kwh": unmet_eps_kwh,
                "valid_min_fraction": valid_min_fraction,
                "mask_block_hours": mask_block_hours_eff,
            },
            "metrics": {
                "htc_target_w_per_k": htc_target,
                "htc_baseline_w_per_k": htc_baseline,
            },
            "monthly": {
                "labels": baseline_summary.get("labels"),
                "baseline_htc_w_per_k": baseline_summary.get("monthly_htc_w_per_k"),
                "mask_reason": {"baseline": baseline_summary.get("mask_reason")},
                "mask_diagnostics": {
                    "baseline": baseline_summary.get("mask_diagnostics")
                },
            },
        }
        return {
            "failed_pre_solve": True,
            "diagnostic_payload": diagnostic_payload,
            "error_message": message,
        }

    scalar = 1.0 + delta_scalar
    fabric_counts: dict[str, int] | None = None

    def _global_probe_transform(model: dict[str, object]) -> dict[str, object]:
        nonlocal fabric_counts
        scaled, counts = apply_fabric_scalar_to_model(model, scalar)
        if fabric_counts is None:
            fabric_counts = counts
        return scaled

    _global_payload, global_summary = run_step(
        f"{label}_global",
        env_overrides={"HEM_ACH_CAL_FACTOR": f"{scalar:.6f}"},
        model_transform=_global_probe_transform,
        mask_override=baseline_mask,
    )
    htc_global = global_summary.get("masked_aggregate_w_per_k")

    # Validate before the sensitivity arithmetic below: a missing metric is
    # None, and subtracting it would raise before this diagnostic could be
    # built.
    missing: list[str] = []
    if not isinstance(htc_baseline, (int, float)):
        missing.append(f"baseline masked monthly {calibration_stat}")
    if not isinstance(htc_global, (int, float)):
        missing.append(f"probe masked monthly {calibration_stat}")
    if missing:
        reason = (
            f"Calibration metrics missing (masked monthly {calibration_stat} "
            "not available)"
        )
        diagnostic_payload = dict(baseline_payload)
        diagnostic_payload["calibration"] = {
            "status": "failed_pre_solve",
            "failure": {"reason": reason, "missing_metrics": missing},
            "masking": {
                "heating_eps_w": heating_eps_w,
                "unmet_eps_kwh": unmet_eps_kwh,
                "valid_min_fraction": valid_min_fraction,
                "mask_block_hours": mask_block_hours_eff,
            },
            "metrics": {
                "htc_target_w_per_k": htc_target,
                "htc_baseline_w_per_k": htc_baseline,
                "htc_global_w_per_k": htc_global,
            },
            "monthly": {
                "labels": baseline_summary.get("labels"),
                "baseline_htc_w_per_k": baseline_summary.get("monthly_htc_w_per_k"),
                "global_htc_w_per_k": global_summary.get("monthly_htc_w_per_k"),
                "mask_reason": {
                    "baseline": baseline_summary.get("mask_reason"),
                    "global": global_summary.get("mask_reason"),
                },
                "mask_diagnostics": {
                    "baseline": baseline_summary.get("mask_diagnostics"),
                    "global": global_summary.get("mask_diagnostics"),
                },
            },
        }
        return {
            "failed_pre_solve": True,
            "diagnostic_payload": diagnostic_payload,
            "error_message": reason,
        }

    s_global = (htc_global - htc_baseline) / delta_scalar if delta_scalar != 0 else 0.0

    lambda_reg_effective = max(lambda_reg, 1e-6)
    solution = solve_single_calibration_scalar(
        htc_baseline=float(htc_baseline),
        htc_target=float(htc_target),
        s_global=s_global,
        lambda_reg=lambda_reg_effective,
        scalar_bounds=scalar_bounds,
    )

    return {
        "failed_pre_solve": False,
        "baseline_payload": baseline_payload,
        "baseline_summary": baseline_summary,
        "baseline_mask": baseline_mask,
        "htc_target": float(htc_target),
        "htc_baseline": float(htc_baseline),
        "htc_global": float(htc_global),
        "global_summary": global_summary,
        "s_global": s_global,
        "fabric_counts": fabric_counts,
        "probe_scalar": scalar,
        "lambda_reg_effective": lambda_reg_effective,
        "calibrated_scalar": float(solution["scalar"]),
    }


def run_iterative_calibration(
    *,
    opt_steps: int,
    opt_tol_w_per_k: float,
    baseline_mask: list[bool],
    label: str,
    htc_target: float,
    delta_scalar: float,
    lambda_reg_effective: float,
    scalar_bounds: tuple[float, float],
    calibrated_scalar: float,
    calibrated_payload: dict,
    calibrated_summary: dict,
    calibrated_counts: dict[str, int] | None,
    cal_env: dict[str, str],
    htc_calibrated: float | None,
    residual: float | None,
    run_step: Callable[..., tuple[dict, dict]],
    run_calibrated_step: Callable[
        ..., tuple[dict, dict, dict[str, int] | None, dict[str, str]]
    ],
) -> dict[str, object]:
    iterative_history: list[dict[str, object]] = []
    iterative_stopped_reason = "disabled"
    iterative_executed_steps = 0
    s_global_final = 0.0

    if opt_steps > 0:
        # Freeze the mask so the optimization objective is stable across steps.
        iteration_mask = list(baseline_mask)
        if residual is not None and abs(residual) <= opt_tol_w_per_k:
            iterative_stopped_reason = "within_tolerance_initial"
        else:
            iterative_stopped_reason = "max_steps_reached"
            for step_no in range(1, opt_steps + 1):
                htc_before = calibrated_summary.get("masked_aggregate_w_per_k")
                scalar_before = calibrated_scalar
                probe_scalar = calibrated_scalar + delta_scalar

                def _probe_transform(
                    model: dict[str, object], _scalar: float = probe_scalar
                ) -> dict[str, object]:
                    scaled, _counts = apply_fabric_scalar_to_model(model, _scalar)
                    return scaled

                _probe_payload, probe_summary = run_step(
                    f"{label}_iter_{step_no}_global",
                    env_overrides={"HEM_ACH_CAL_FACTOR": f"{probe_scalar:.6f}"},
                    model_transform=_probe_transform,
                    mask_override=iteration_mask,
                )

                htc_probe = probe_summary.get("masked_aggregate_w_per_k")
                if not all(
                    isinstance(v, (int, float)) for v in (htc_before, htc_probe)
                ):
                    iterative_stopped_reason = "metrics_missing"
                    break

                s_global_step = (
                    (float(htc_probe) - float(htc_before)) / delta_scalar
                    if delta_scalar != 0
                    else 0.0
                )
                s_global_final = s_global_step
                increment = solve_single_calibration_increment(
                    htc_baseline=float(htc_before),
                    htc_target=float(htc_target),
                    s_global=s_global_step,
                    lambda_reg=lambda_reg_effective,
                    scalar_current=calibrated_scalar,
                    scalar_bounds=scalar_bounds,
                )
                delta_applied = float(increment["delta_scalar"])
                calibrated_scalar = float(increment["scalar"])

                (
                    calibrated_payload,
                    calibrated_summary,
                    calibrated_counts,
                    cal_env,
                ) = run_calibrated_step(
                    batch_suffix=f"iter_{step_no}_calibrated",
                    mask_override=iteration_mask,
                    scalar=calibrated_scalar,
                )

                htc_calibrated = calibrated_summary.get("masked_aggregate_w_per_k")
                residual = (
                    float(htc_calibrated) - float(htc_target)
                    if isinstance(htc_calibrated, (int, float))
                    else None
                )

                iterative_history.append(
                    {
                        "step": step_no,
                        "scalar_before": float(scalar_before),
                        "scalar_after": float(calibrated_scalar),
                        "delta_scalar_applied": delta_applied,
                        "htc_before_w_per_k": float(htc_before),
                        "htc_after_w_per_k": (
                            float(htc_calibrated)
                            if isinstance(htc_calibrated, (int, float))
                            else None
                        ),
                        "residual_after_w_per_k": residual,
                        "sensitivity_s_global": float(s_global_step),
                    }
                )
                iterative_executed_steps = step_no

                if residual is not None and abs(residual) <= opt_tol_w_per_k:
                    iterative_stopped_reason = "within_tolerance"
                    break
                if abs(delta_applied) <= 1e-6:
                    iterative_stopped_reason = "zero_increment"
                    break

    return {
        "calibrated_scalar": calibrated_scalar,
        "calibrated_payload": calibrated_payload,
        "calibrated_summary": calibrated_summary,
        "calibrated_counts": calibrated_counts,
        "cal_env": cal_env,
        "htc_calibrated": htc_calibrated,
        "residual": residual,
        "iterative_history": iterative_history,
        "iterative_stopped_reason": iterative_stopped_reason,
        "iterative_executed_steps": iterative_executed_steps,
        "s_global_final": s_global_final,
    }
