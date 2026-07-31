# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

from pathlib import Path

from htc_harness import compute_design_htc, read_results_static
from htc_harness.empirical import (
    solve_single_calibration_increment,
    solve_single_calibration_scalar,
)


def test_read_results_static_and_design_htc(tmp_path: Path) -> None:
    path = tmp_path / "results_static.csv"
    path.write_text(
        "\n".join(
            [
                "Heat transfer coefficient,W / K,150.0",
                "Heat loss parameter,W / m2.K,2.5",
                "Heat capacity parameter,kJ / m2.K,100.0",
                "Heat loss form factor,,1.1",
                "Assumptions used for HTC/HLP calculation:",
                "Internal air temperature,Celsius,20",
                "External air temperature,Celsius,-1",
            ]
        )
    )
    values = read_results_static(path)
    assert values["Heat transfer coefficient"] == 150.0
    htc_design = compute_design_htc(values)
    assert htc_design == 150.0


def test_solve_single_calibration_scalar_and_increment() -> None:
    first = solve_single_calibration_scalar(
        htc_baseline=100.0,
        htc_target=120.0,
        s_global=8.0,
        lambda_reg=0.1,
        scalar_bounds=(0.1, 2.5),
    )
    assert 0.1 <= first["scalar"] <= 2.5

    nxt = solve_single_calibration_increment(
        htc_baseline=110.0,
        htc_target=120.0,
        s_global=8.0,
        lambda_reg=0.1,
        scalar_current=first["scalar"],
        scalar_bounds=(0.1, 2.5),
    )
    assert 0.1 <= nxt["scalar"] <= 2.5
    assert nxt["delta_scalar"] == nxt["scalar"] - first["scalar"]
