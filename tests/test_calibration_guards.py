# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
"""Pre-solve guards in `run_initial_calibration`.

Calibration builds a `failed_pre_solve` diagnostic payload when it cannot form
its objective. That payload is the whole point of the branch, so these tests
check it is actually reached rather than pre-empted by a raw TypeError from
arithmetic on a missing metric.
"""

from __future__ import annotations

from typing import Any

import pytest

from htc_harness.calibration import run_initial_calibration

MEASURED = {"measured_htc": {"htc_value_w_per_k": 200.0}}


def _solve(summary: dict[str, Any], mask: list[bool] | None) -> dict[str, Any]:
    return run_initial_calibration(
        label="t",
        delta_scalar=0.2,
        lambda_reg=0.01,
        scalar_bounds=(0.1, 2.5),
        heating_eps_w=0.0,
        unmet_eps_kwh=0.0,
        valid_min_fraction=0.95,
        mask_block_hours_eff=24.0,
        calibration_stat="mean",
        extract_mask_fn=lambda payload, **kwargs: (mask, "no unmet-demand series"),
        run_step=lambda label, **kwargs: (MEASURED, summary),
    )


def _failure(result: dict[str, Any]) -> dict[str, Any]:
    return result["diagnostic_payload"]["calibration"]["failure"]


def test_missing_metric_reports_diagnostics_instead_of_raising() -> None:
    result = _solve(
        {"masked_aggregate_w_per_k": None, "labels": [], "monthly_htc_w_per_k": []},
        [True],
    )

    assert result["failed_pre_solve"] is True
    assert result["diagnostic_payload"]["calibration"]["status"] == "failed_pre_solve"
    missing = _failure(result)["missing_metrics"]
    assert "baseline masked monthly mean" in missing


def test_missing_mask_is_a_pre_solve_failure() -> None:
    """A mask is required even when the aggregate is a number.

    With no maskable series the aggregate falls back to an *unmasked* value, so
    the solve would silently optimise a different objective from the one it
    reports — and iterative refinement would then fail freezing a `None` mask.
    """
    runs: list[str] = []
    summary = {
        "masked_aggregate_w_per_k": 150.0,
        "labels": [],
        "monthly_htc_w_per_k": [],
    }

    def run_step(label: str, **kwargs: Any) -> tuple[dict, dict]:
        runs.append(label)
        return MEASURED, summary

    result = run_initial_calibration(
        label="t",
        delta_scalar=0.2,
        lambda_reg=0.01,
        scalar_bounds=(0.1, 2.5),
        heating_eps_w=0.0,
        unmet_eps_kwh=0.0,
        valid_min_fraction=0.95,
        mask_block_hours_eff=24.0,
        calibration_stat="mean",
        extract_mask_fn=lambda payload, **kwargs: (None, "no unmet-demand series"),
        run_step=run_step,
    )

    assert result["failed_pre_solve"] is True
    assert _failure(result)["missing_metrics"] == ["baseline timestep mask"]
    # The user-facing message must name the real cause, not the monthly
    # aggregate — which was present here.
    assert "needs a per-timestep mask" in result["error_message"]
    assert "no unmet-demand series" in result["error_message"]
    # The probe is a full extra HEM batch; it must not run for a failure that
    # was knowable straight after the baseline.
    assert runs == ["t_base"]


def test_usable_metrics_solve_and_carry_the_mask_forward() -> None:
    result = _solve(
        {"masked_aggregate_w_per_k": 150.0, "labels": [], "monthly_htc_w_per_k": []},
        [True, False],
    )

    assert result["failed_pre_solve"] is False
    # Sensitivity is (probe - baseline) / delta; both runs return the same
    # stubbed summary here, so it is flat.
    assert result["s_global"] == 0.0
    assert result["htc_baseline"] == 150.0
    assert result["htc_target"] == 200.0
    # The frozen mask must survive for the iterative stage.
    assert result["baseline_mask"] == [True, False]


def test_missing_measured_value_fails_before_any_probe_run() -> None:
    runs: list[str] = []

    def run_step(label: str, **kwargs: Any) -> tuple[dict, dict]:
        runs.append(label)
        return {"measured_htc": {}}, {}

    result = run_initial_calibration(
        label="t",
        delta_scalar=0.2,
        lambda_reg=0.01,
        scalar_bounds=(0.1, 2.5),
        heating_eps_w=0.0,
        unmet_eps_kwh=0.0,
        valid_min_fraction=0.95,
        mask_block_hours_eff=24.0,
        calibration_stat="mean",
        extract_mask_fn=lambda payload, **kwargs: ([True], ""),
        run_step=run_step,
    )

    assert result["failed_pre_solve"] is True
    assert "Measured HTC value is missing" in result["error_message"]
    # Only the baseline ran; no engine time spent on a probe that cannot help.
    assert runs == ["t_base"]


def test_iterative_refinement_walks_the_scalar_toward_the_target() -> None:
    """One scalar drives both the fabric transform and HEM_ACH_CAL_FACTOR.

    Exercises the refinement loop end to end against a linear stand-in for the
    engine, so the single-scalar plumbing stays wired together.
    """
    from htc_harness.calibration import run_iterative_calibration

    target, baseline, sensitivity = 200.0, 150.0, 40.0

    def htc_for(scalar: float) -> float:
        return baseline + sensitivity * (scalar - 1.0)

    def run_step(label: str, **kwargs: Any) -> tuple[dict, dict]:
        scalar = float(kwargs["env_overrides"]["HEM_ACH_CAL_FACTOR"])
        return {}, {"masked_aggregate_w_per_k": htc_for(scalar)}

    def run_calibrated_step(
        *, batch_suffix: str, mask_override: list[bool] | None, scalar: float
    ) -> tuple[dict, dict, dict[str, int] | None, dict[str, str]]:
        return (
            {},
            {"masked_aggregate_w_per_k": htc_for(scalar)},
            {"thermal_bridging_scaled": 3},
            {"HEM_ACH_CAL_FACTOR": f"{scalar:.6f}"},
        )

    start = 1.0
    result = run_iterative_calibration(
        opt_steps=8,
        opt_tol_w_per_k=0.5,
        baseline_mask=[True, True],
        label="cal",
        htc_target=target,
        delta_scalar=0.2,
        lambda_reg_effective=1e-6,
        scalar_bounds=(0.1, 2.5),
        calibrated_scalar=start,
        calibrated_payload={},
        calibrated_summary={"masked_aggregate_w_per_k": htc_for(start)},
        calibrated_counts=None,
        cal_env={},
        htc_calibrated=htc_for(start),
        residual=htc_for(start) - target,
        run_step=run_step,
        run_calibrated_step=run_calibrated_step,
    )

    assert result["iterative_stopped_reason"] == "within_tolerance"
    assert abs(result["residual"]) <= 0.5
    # Target is 50 W/K above baseline at a sensitivity of 40 W/K per unit.
    assert abs(result["calibrated_scalar"] - 2.25) < 0.01
    assert result["s_global_final"] == pytest.approx(sensitivity)
    history = result["iterative_history"]
    assert result["iterative_executed_steps"] == len(history)
    assert [h["step"] for h in history] == list(range(1, len(history) + 1))


def test_iterative_refinement_is_a_no_op_when_disabled() -> None:
    from htc_harness.calibration import run_iterative_calibration

    def explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no engine run may happen with opt_steps=0")

    result = run_iterative_calibration(
        opt_steps=0,
        opt_tol_w_per_k=0.5,
        baseline_mask=[True],
        label="cal",
        htc_target=200.0,
        delta_scalar=0.2,
        lambda_reg_effective=1e-6,
        scalar_bounds=(0.1, 2.5),
        calibrated_scalar=1.3,
        calibrated_payload={},
        calibrated_summary={},
        calibrated_counts=None,
        cal_env={},
        htc_calibrated=190.0,
        residual=-10.0,
        run_step=explode,
        run_calibrated_step=explode,
    )

    assert result["iterative_stopped_reason"] == "disabled"
    assert result["iterative_executed_steps"] == 0
    assert result["calibrated_scalar"] == 1.3
