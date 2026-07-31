# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

from pathlib import Path

import pytest

from htc_harness import Timeseries, read_csv_timeseries
from htc_harness.plotting import _daily_mean_series, _linear_fit_slope, _percentile
from htc_harness.reader import (
    apply_fixed_instant_electric_heating_to_model,
    offset_epw_dry_bulb,
)


def _make_timeseries() -> Timeseries:
    return Timeseries(
        columns=["A", "B", "C"],
        units=["-", "-", "-"],
        rows=[
            [1.0, 0.0, None],
            [2.0, 5.0, 1.0],
            [3.0, -1.0, 2.0],
        ],
    )


def test_timeseries_get_column() -> None:
    ts = _make_timeseries()
    assert ts.get_column("A") == [1.0, 2.0, 3.0]
    with pytest.raises(ValueError):
        ts.get_column("missing")


def test_read_csv_timeseries_no_units(tmp_path: Path) -> None:
    path = tmp_path / "simple.csv"
    path.write_text("A,B\n1,2\n3,4\n")
    ts = read_csv_timeseries(path, has_units=False)
    assert ts.columns == ["A", "B"]
    assert ts.units == []
    assert ts.rows == [[1.0, 2.0], [3.0, 4.0]]


def test_offset_epw_dry_bulb(tmp_path: Path) -> None:
    source = tmp_path / "source.epw"
    target = tmp_path / "target.epw"
    header = ["H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8"]
    data = (
        "2020,1,1,1,60,0,10.0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0"
    )
    source.write_text("\n".join(header + [data]) + "\n")
    offset_epw_dry_bulb(source, target, 2.5)
    lines = target.read_text().splitlines()
    assert lines[8].split(",")[6] == "12.5"


def test_percentile_basic() -> None:
    values = [10.0, 2.0, 5.0, 8.0, 1.0]
    assert _percentile(values, 0) == 1.0
    assert _percentile(values, 100) == 10.0
    assert _percentile(values, 50) in {5.0, 8.0}


def test_linear_fit_slope() -> None:
    x = [0.0, 1.0, 2.0]
    y = [1.0, 3.0, 5.0]
    assert _linear_fit_slope(x, y) == 2.0


def test_daily_mean_series() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    xs, ys = _daily_mean_series(values, timestep_hours=12.0)
    assert xs == [0, 2]
    assert ys == [1.5, 3.5]


def test_apply_fixed_instant_electric_heating_to_model_replaces_zone_systems() -> None:
    model = {
        "Zone": {
            "Living": {"SpaceHeatSystem": "old_living"},
            "Bed1": {"SpaceHeatSystem": "old_bed1"},
        },
        "SpaceHeatSystem": {"old_living": {"type": "WetDistribution"}},
        "EnergySupply": {"mains elec": {"fuel": "electricity"}},
    }

    transformed, info = apply_fixed_instant_electric_heating_to_model(model, 20.0)

    assert (
        transformed["Zone"]["Living"]["SpaceHeatSystem"]
        == "Living_fixed_electric_heating"
    )
    assert (
        transformed["Zone"]["Bed1"]["SpaceHeatSystem"] == "Bed1_fixed_electric_heating"
    )
    assert transformed["SpaceHeatSystem"]["Living_fixed_electric_heating"]["type"] == (
        "InstantElecHeater"
    )
    assert (
        transformed["SpaceHeatSystem"]["Living_fixed_electric_heating"][
            "frac_convective"
        ]
        == 1.0
    )
    assert (
        transformed["SpaceHeatSystem"]["Living_fixed_electric_heating"]["rated_power"]
        == 20.0
    )
    assert info["zones_replaced"] == 2
    assert info["energy_supply_name"] == "mains elec"
    assert not info["energy_supply_created"]


def test_apply_fixed_instant_electric_heating_to_model_creates_electric_supply() -> (
    None
):
    model = {
        "Zone": {"Living": {}},
        "SpaceHeatSystem": {"old": {"type": "Boiler"}},
        "EnergySupply": {"mains gas": {"fuel": "mains_gas"}},
    }

    transformed, info = apply_fixed_instant_electric_heating_to_model(model, 15.0)

    assert "mains elec" in transformed["EnergySupply"]
    assert transformed["EnergySupply"]["mains elec"]["fuel"] == "electricity"
    assert transformed["SpaceHeatSystem"]["Living_fixed_electric_heating"][
        "EnergySupply"
    ] == ("mains elec")
    assert info["energy_supply_created"]
