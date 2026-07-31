# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

from htc_harness.empirical import masked_monthly_htc_from_summary


def _build_payload() -> dict[str, object]:
    # Three one-step "months" with HTC values [100, 100, 300].
    # For each block: Q(offset=0) = 1000 Wh, Q(offset=1) = 1000 - HTC.
    return {
        "job": {
            "window": {"timestep_hours": 1.0},
            "offsets_k": [0.0, 1.0],
        },
        "derived": {
            "month_bins": [
                {"start": 0, "end": 1, "label": "M1"},
                {"start": 1, "end": 2, "label": "M2"},
                {"start": 2, "end": 3, "label": "M3"},
            ],
            "q_loss_w_by_offset_k": {
                "0.0": [1000.0, 1000.0, 1000.0],
                "1.0": [900.0, 900.0, 700.0],
            },
        },
    }


def test_masked_monthly_aggregate_stat_mean_vs_median() -> None:
    payload = _build_payload()
    mask = [True, True, True]

    monthly_mean = masked_monthly_htc_from_summary(
        payload, mask_override=mask, aggregate_stat="mean"
    )
    monthly_median = masked_monthly_htc_from_summary(
        payload, mask_override=mask, aggregate_stat="median"
    )

    assert monthly_mean["masked_aggregate_stat"] == "mean"
    assert monthly_median["masked_aggregate_stat"] == "median"
    assert monthly_mean["monthly_htc_w_per_k"] == [100.0, 100.0, 300.0]
    assert monthly_median["monthly_htc_w_per_k"] == [100.0, 100.0, 300.0]
    assert monthly_mean["masked_aggregate_w_per_k"] == 500.0 / 3.0
    assert monthly_median["masked_aggregate_w_per_k"] == 100.0


def test_masked_monthly_default_and_invalid_stat_fall_back_to_mean() -> None:
    payload = _build_payload()
    mask = [True, True, True]

    monthly_default = masked_monthly_htc_from_summary(payload, mask_override=mask)
    monthly_invalid = masked_monthly_htc_from_summary(
        payload, mask_override=mask, aggregate_stat="not-a-stat"
    )

    assert monthly_default["masked_aggregate_stat"] == "mean"
    assert monthly_invalid["masked_aggregate_stat"] == "mean"
    assert monthly_default["masked_aggregate_w_per_k"] == 500.0 / 3.0
    assert monthly_invalid["masked_aggregate_w_per_k"] == 500.0 / 3.0


def test_masked_monthly_does_not_apply_second_stage_monthly_drop() -> None:
    # Two timesteps per month with one passing timestep each month:
    # valid fraction is 0.5 in all months, and each month still has finite HTC.
    payload = {
        "job": {
            "window": {"timestep_hours": 1.0},
            "offsets_k": [0.0, 1.0],
        },
        "derived": {
            "month_bins": [
                {"start": 0, "end": 2, "label": "M1"},
                {"start": 2, "end": 4, "label": "M2"},
                {"start": 4, "end": 6, "label": "M3"},
            ],
            "q_loss_w_by_offset_k": {
                "0.0": [1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0],
                "1.0": [900.0, 900.0, 900.0, 900.0, 700.0, 700.0],
            },
        },
    }
    mask = [True, False, True, False, True, False]

    summary = masked_monthly_htc_from_summary(
        payload,
        mask_override=mask,
        aggregate_stat="mean",
        valid_min_fraction=0.95,
        mask_block_hours=None,
    )

    assert summary["monthly_htc_w_per_k"] == [100.0, 100.0, 300.0]
    assert summary["masked_aggregate_w_per_k"] == 500.0 / 3.0


def test_masked_monthly_reports_reason_when_no_finite_values() -> None:
    payload = _build_payload()
    mask = [False, False, False]

    summary = masked_monthly_htc_from_summary(
        payload,
        mask_override=mask,
        aggregate_stat="mean",
        valid_min_fraction=0.95,
        mask_block_hours=24.0,
    )

    assert summary["masked_aggregate_w_per_k"] is None
    assert isinstance(summary["mask_reason"], str)
    assert "No finite monthly HTC values" in summary["mask_reason"]
    diagnostics = summary["mask_diagnostics"]
    assert diagnostics["n_months_total"] == 3
    assert diagnostics["n_months_finite"] == 0
