# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
"""Model transforms applied before a run.

`apply_fabric_scalar_to_model` is what `h3 calibrate` actually adjusts, so a
term it silently fails to scale is a term calibration cannot use. These tests
pin the shapes it must recognise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from htc_harness.reader import (
    apply_fabric_scalar_to_model,
    set_simulation_period_on_model,
)

DEMO_MODEL = Path(__file__).parent / "fixtures" / "demo_model.json"


def _demo_model() -> dict:
    return json.loads(DEMO_MODEL.read_text())


def test_thermal_bridges_are_scaled_in_the_current_engine_schema() -> None:
    """Bridges are a mapping of name -> bridge, each tagged with `type`.

    An earlier version looked for keys literally named `ThermalBridgeLinear` on
    the `ThermalBridging` object itself. Real models name those keys after the
    bridge, so nothing matched and every bridge went unscaled while the counts
    still reported success.
    """
    model = _demo_model()
    before = model["Zone"]["zone1_livingroom"]["ThermalBridging"]
    original = {
        name: entry["linear_thermal_transmittance"]
        for name, entry in before.items()
        if entry.get("type") == "ThermalBridgeLinear"
    }
    assert original, "fixture must contain linear thermal bridges"

    scaled, counts = apply_fabric_scalar_to_model(model, 2.0)

    assert counts["thermal_bridging_scaled"] > 0
    after = scaled["Zone"]["zone1_livingroom"]["ThermalBridging"]
    for name, value in original.items():
        assert after[name]["linear_thermal_transmittance"] == value * 2.0
    # The source model must not be mutated.
    assert before == model["Zone"]["zone1_livingroom"]["ThermalBridging"]


def test_thermal_bridge_point_is_scaled() -> None:
    model = {
        "Zone": {
            "z": {
                "ThermalBridging": {
                    "TB1": {"type": "ThermalBridgePoint", "heat_transfer_coeff": 0.4}
                }
            }
        }
    }
    scaled, counts = apply_fabric_scalar_to_model(model, 0.5)

    assert counts["thermal_bridging_scaled"] == 1
    bridge = scaled["Zone"]["z"]["ThermalBridging"]["TB1"]
    assert bridge["heat_transfer_coeff"] == 0.2


def test_legacy_thermal_bridging_shapes_still_scale() -> None:
    """Older models expressed bridges as a bare number or a wrapped object."""
    bare, bare_counts = apply_fabric_scalar_to_model(
        {"Zone": {"z": {"ThermalBridging": 2.0}}}, 3.0
    )
    assert bare["Zone"]["z"]["ThermalBridging"] == 6.0
    assert bare_counts["thermal_bridging_scaled"] == 1

    wrapped, wrapped_counts = apply_fabric_scalar_to_model(
        {
            "Zone": {
                "z": {
                    "ThermalBridging": {
                        "ThermalBridgeLinear": {"linear_thermal_transmittance": 0.1}
                    }
                }
            }
        },
        3.0,
    )
    linear = wrapped["Zone"]["z"]["ThermalBridging"]["ThermalBridgeLinear"]
    assert linear["linear_thermal_transmittance"] == 0.30000000000000004
    assert wrapped_counts["thermal_bridging_scaled"] == 1


def test_non_bridge_entries_are_left_alone() -> None:
    model = {"Zone": {"z": {"ThermalBridging": {"TB1": {"type": "Unknown", "x": 1.0}}}}}
    scaled, counts = apply_fabric_scalar_to_model(model, 2.0)

    assert counts["thermal_bridging_scaled"] == 0
    assert scaled["Zone"]["z"]["ThermalBridging"]["TB1"] == {
        "type": "Unknown",
        "x": 1.0,
    }


def test_building_elements_and_bridges_both_scale_in_one_pass() -> None:
    """Calibration moves fabric and bridges together; neither may be skipped."""
    _, counts = apply_fabric_scalar_to_model(_demo_model(), 1.1)

    assert counts["thermal_bridging_scaled"] > 0
    assert counts["building_elements_scaled"] > 0


def test_a_bridge_named_after_its_type_does_not_hide_its_siblings() -> None:
    """The legacy-shape check must not misread a current-schema mapping.

    A mapping whose keys happen to include `ThermalBridgeLinear` looks like the
    legacy wrapper shape. Reading it that way scaled that one entry and
    returned, silently leaving every sibling unscaled while the count reported
    success — the same failure mode as the bug this file exists to pin.
    """
    model = {
        "Zone": {
            "z": {
                "ThermalBridging": {
                    "ThermalBridgeLinear": {
                        "type": "ThermalBridgeLinear",
                        "linear_thermal_transmittance": 0.1,
                    },
                    "Z1_tbr_E2": {
                        "type": "ThermalBridgeLinear",
                        "linear_thermal_transmittance": 0.2,
                    },
                    "Z1_tbr_E3": {
                        "type": "ThermalBridgePoint",
                        "heat_transfer_coeff": 0.3,
                    },
                }
            }
        }
    }
    scaled, counts = apply_fabric_scalar_to_model(model, 2.0)

    assert counts["thermal_bridging_scaled"] == 3
    bridges = scaled["Zone"]["z"]["ThermalBridging"]
    assert bridges["ThermalBridgeLinear"]["linear_thermal_transmittance"] == 0.2
    assert bridges["Z1_tbr_E2"]["linear_thermal_transmittance"] == 0.4
    assert bridges["Z1_tbr_E3"]["heat_transfer_coeff"] == 0.6


def _sandbox_model() -> dict:
    """A model shaped like the demo's sandbox dwelling.

    Deliberately a fixture rather than the real `example_semi_detached.json`,
    which lives in another repository: these assertions are about the transform's
    arithmetic, and they should not depend on a file this repo does not own or on
    whether a sibling checkout happens to be present. The real model is what the
    browser sandbox controls exercise.

    Carries one of every element category the transform discriminates between,
    including the two it must leave alone.
    """
    return {
        "GroundFloorArea": 35.6,
        "BuildingLength": 8.9,
        "BuildingWidth": 4,
        "Zone": {
            "Zone 1": {
                "volume": 199.36,
                "livingroom_area": 12.86,
                "restofdwelling_area": 58.34,
                "ThermalBridging": 26.042,
                "BuildingElement": {
                    "Wall (S)": {
                        "type": "BuildingElementOpaque",
                        "pitch": 90,
                        "u_value": 0.18,
                        "area": 7.39,
                        "width": 4,
                    },
                    "Door": {
                        "type": "BuildingElementOpaque",
                        "pitch": 90,
                        "is_external_door": True,
                        "u_value": 1.0,
                        "area": 2.2,
                        "width": 1.05,
                    },
                    "Roof": {
                        "type": "BuildingElementOpaque",
                        "pitch": 40,
                        "is_unheated_pitched_roof": True,
                        "u_value": 0.19,
                        "area": 25.0,
                        "width": 4,
                    },
                    "Window": {
                        "type": "BuildingElementTransparent",
                        "u_value": 1.2,
                        "width": 1.24,
                        "height": 1.3,
                        "base_height": 1.0,
                        "mid_height": 1.65,
                    },
                    "Floor": {
                        "type": "BuildingElementGround",
                        "u_value": 0.13,
                        "area": 35.6,
                        "total_area": 30,
                        "perimeter": 12.0,
                    },
                    "Party Wall (E)": {
                        "type": "BuildingElementPartyWall",
                        "u_value": 0.15,
                        "area": 24.92,
                    },
                },
            }
        },
        "InfiltrationVentilation": {
            "Leaks": {"test_result": 4, "env_area": 215.65},
            "MechanicalVentilation": {
                "MEV": {
                    "design_outdoor_air_flow_rate": 120,
                    "measured_air_flow_rate": 120,
                    "measured_fan_power": 44.16,
                }
            },
        },
    }


def test_shoebox_levers_set_only_their_own_element_category() -> None:
    """A wall control that also moved the doors or party walls would be a lie.

    Party walls in particular measurably do nothing to HTC, so quietly
    including them would change the model without changing the answer.
    """
    from htc_harness.reader import ShoeboxLevers, apply_shoebox_levers_to_model

    model = _sandbox_model()
    out, info = apply_shoebox_levers_to_model(
        model, ShoeboxLevers(wall_u_value=0.9, roof_u_value=1.1, glazing_u_value=3.3)
    )
    elements = out["Zone"]["Zone 1"]["BuildingElement"]

    assert info["elements_set"]["walls"] >= 1
    assert info["elements_set"]["roofs"] >= 1
    assert info["elements_set"]["glazing"] >= 1

    for element in elements.values():
        kind = element.get("type")
        if kind == "BuildingElementOpaque" and element.get("is_external_door"):
            assert element["u_value"] == 1.0, "a door is not a wall"
        elif kind == "BuildingElementOpaque" and element.get(
            "is_unheated_pitched_roof"
        ):
            assert element["u_value"] == 1.1
        elif kind == "BuildingElementOpaque" and element.get("pitch") == 90:
            assert element["u_value"] == 0.9
        elif kind == "BuildingElementTransparent":
            assert element["u_value"] == 3.3
        elif kind == "BuildingElementPartyWall":
            assert element["u_value"] == 0.15, "party walls must be left alone"
        elif kind == "BuildingElementGround":
            assert element["u_value"] == 0.13, "the floor is not a wall"


def test_shoebox_size_scales_areas_and_runs_at_different_powers() -> None:
    """Plan areas go as k; perimeters and wall runs go as sqrt(k).

    Scaling everything by k would give a dwelling whose walls and floor
    disagree about how big it is.
    """
    import math

    from htc_harness.reader import ShoeboxLevers, apply_shoebox_levers_to_model

    model = _sandbox_model()
    base_area = model["GroundFloorArea"]
    k = 4.0
    out, info = apply_shoebox_levers_to_model(
        model, ShoeboxLevers(floor_area_m2=base_area * k)
    )
    assert info["floor_area_scale"] == k

    zone_in = model["Zone"]["Zone 1"]
    zone_out = out["Zone"]["Zone 1"]
    root = math.sqrt(k)

    assert out["GroundFloorArea"] == pytest.approx(base_area * k)
    assert zone_out["volume"] == pytest.approx(zone_in["volume"] * k)
    # A length-driven total follows the perimeter, not the floor area.
    assert zone_out["ThermalBridging"] == pytest.approx(
        zone_in["ThermalBridging"] * root
    )

    for name, element in zone_out["BuildingElement"].items():
        original = zone_in["BuildingElement"][name]
        if element.get("type") == "BuildingElementGround":
            assert element["area"] == pytest.approx(original["area"] * k)
            assert element["perimeter"] == pytest.approx(original["perimeter"] * root)
        elif element.get("is_external_door"):
            assert element["area"] == pytest.approx(original["area"]), "doors are fixed"
        elif element.get("is_unheated_pitched_roof"):
            assert element["area"] == pytest.approx(original["area"] * k)
        elif element.get("type") == "BuildingElementOpaque":
            assert element["area"] == pytest.approx(original["area"] * root)


def test_shoebox_size_never_scales_ventilation_downward() -> None:
    """Part F sets a floor on extract rate, and the model already sits at it.

    Scaling ventilation down with a shrinking dwelling fails FHS validation
    outright; not scaling it up at all fails once the dwelling grows past
    roughly 1.5x. Both were observed against the real engine.
    """
    from htc_harness.reader import ShoeboxLevers, apply_shoebox_levers_to_model

    model = _sandbox_model()
    base = model["InfiltrationVentilation"]["MechanicalVentilation"]
    base_flow = next(iter(base.values()))["design_outdoor_air_flow_rate"]

    shrunk, _ = apply_shoebox_levers_to_model(
        model, ShoeboxLevers(floor_area_m2=model["GroundFloorArea"] * 0.5)
    )
    flow = next(
        iter(shrunk["InfiltrationVentilation"]["MechanicalVentilation"].values())
    )["design_outdoor_air_flow_rate"]
    assert flow == pytest.approx(base_flow), "must not drop below the Part F floor"

    grown, _ = apply_shoebox_levers_to_model(
        model, ShoeboxLevers(floor_area_m2=model["GroundFloorArea"] * 2)
    )
    flow = next(
        iter(grown["InfiltrationVentilation"]["MechanicalVentilation"].values())
    )["design_outdoor_air_flow_rate"]
    assert flow == pytest.approx(base_flow * 2)


def test_shoebox_glazing_area_widens_openings_without_moving_them() -> None:
    """Height stays put, so mid_height and base_height cannot fall out of step."""
    from htc_harness.reader import ShoeboxLevers, apply_shoebox_levers_to_model

    model = _sandbox_model()
    zone = model["Zone"]["Zone 1"]["BuildingElement"]
    glazed = sum(
        e["width"] * e["height"]
        for e in zone.values()
        if e.get("type") == "BuildingElementTransparent"
    )

    out, _ = apply_shoebox_levers_to_model(
        model, ShoeboxLevers(glazing_area_m2=glazed * 3)
    )
    out_zone = out["Zone"]["Zone 1"]["BuildingElement"]
    new_glazed = sum(
        e["width"] * e["height"]
        for e in out_zone.values()
        if e.get("type") == "BuildingElementTransparent"
    )
    assert new_glazed == pytest.approx(glazed * 3)

    for name, element in out_zone.items():
        if element.get("type") != "BuildingElementTransparent":
            continue
        original = zone[name]
        assert element["height"] == pytest.approx(original["height"])
        for key in ("base_height", "mid_height"):
            if key in original:
                assert element[key] == pytest.approx(original[key])


def test_shoebox_thermal_bridging_is_absolute_not_rescaled_by_size() -> None:
    """Size scaling drags bridging with it, so an explicit value must win.

    Otherwise setting 26 W/K on a doubled dwelling would silently store 37.
    """
    from htc_harness.reader import ShoeboxLevers, apply_shoebox_levers_to_model

    model = _sandbox_model()
    out, _ = apply_shoebox_levers_to_model(
        model,
        ShoeboxLevers(
            floor_area_m2=model["GroundFloorArea"] * 4,
            thermal_bridging_w_per_k=26.0,
        ),
    )
    assert out["Zone"]["Zone 1"]["ThermalBridging"] == pytest.approx(26.0)


def test_shoebox_levers_leave_unset_attributes_alone() -> None:
    from htc_harness.reader import ShoeboxLevers, apply_shoebox_levers_to_model

    model = _sandbox_model()
    out, info = apply_shoebox_levers_to_model(model, ShoeboxLevers())
    assert out == model
    assert info["elements_set"] == {"walls": 0, "roofs": 0, "glazing": 0}


def test_simulation_period_replaces_whatever_the_wrapper_wrote() -> None:
    """Hours from the start of the year, matching HEM's own SimulationTime."""
    model = {"SimulationTime": {"start": 0.0, "end": 8760.0, "step": 0.5}, "Zone": {}}
    out, info = set_simulation_period_on_model(
        model, start_hour=0, end_hour=2160, step_hours=0.5
    )

    assert out["SimulationTime"] == {"start": 0.0, "end": 2160.0, "step": 0.5}
    assert info["previous"] == {"start": 0.0, "end": 8760.0, "step": 0.5}
    # The caller's model is untouched, like every other transform here.
    assert model["SimulationTime"]["end"] == 8760.0


def test_simulation_period_is_added_when_the_model_has_none() -> None:
    """The authored sandbox model carries no period; the wrapper adds one."""
    out, info = set_simulation_period_on_model(
        {"Zone": {}}, start_hour=0, end_hour=2160, step_hours=0.5
    )
    assert out["SimulationTime"]["end"] == 2160.0
    assert info["previous"] is None


@pytest.mark.parametrize(
    ("start", "end", "step"),
    [(0, 0, 0.5), (2160, 0, 0.5), (0, 2160, 0)],
)
def test_simulation_period_rejects_an_empty_or_stepless_run(
    start: float, end: float, step: float
) -> None:
    """An end before its start would run zero timesteps and report an empty series."""
    with pytest.raises(ValueError):
        set_simulation_period_on_model(
            {"Zone": {}}, start_hour=start, end_hour=end, step_hours=step
        )


def test_preprocessed_fixed_electric_writes_the_core_schema_shape() -> None:
    """The two schemas disagree, so the shape depends on who validates it.

    The FHS input schema requires `convective_type` and forbids
    `frac_convective`; the core schema is the exact reverse and also requires
    `Control`. A model carrying both fields is refused by both engines, which
    is what running the sandbox against the real WASM build found.
    """
    from htc_harness.reader import (
        apply_fixed_instant_electric_heating_to_preprocessed_model,
    )

    model = {
        "EnergySupply": {"mains elec": {"fuel": "electricity"}},
        "SpaceHeatSystem": {
            "Zone 1 circuit": {
                "type": "WetDistribution",
                "Control": "HeatingPattern_Zone 1 circuit",
                "emitters": [{"frac_convective": 0.4}],
            }
        },
    }
    out, info = apply_fixed_instant_electric_heating_to_preprocessed_model(
        model, rated_power_kw_per_zone=20.0
    )

    assert info["systems_replaced"] == 1
    system = out["SpaceHeatSystem"]["Zone 1 circuit"]
    assert system == {
        "type": "InstantElecHeater",
        "rated_power": 20.0,
        "frac_convective": 1.0,
        "EnergySupply": "mains elec",
        # The control the flattening just held at setpoint, kept rather than
        # invented: the core schema requires one, and a new one would restore a
        # schedule this route exists to remove.
        "Control": "HeatingPattern_Zone 1 circuit",
    }
    assert "convective_type" not in system
    # The system keeps its name, so anything referencing it still resolves.
    assert list(out["SpaceHeatSystem"]) == ["Zone 1 circuit"]


def test_preprocessed_fixed_electric_leaves_uncontrolled_systems_alone() -> None:
    """Replacing one would give the zone a heater nothing ever turns off."""
    from htc_harness.reader import (
        apply_fixed_instant_electric_heating_to_preprocessed_model,
    )

    out, info = apply_fixed_instant_electric_heating_to_preprocessed_model(
        {"SpaceHeatSystem": {"orphan": {"type": "WetDistribution"}}},
        rated_power_kw_per_zone=20.0,
    )
    assert info["systems_replaced"] == 0
    assert info["systems_without_control"] == ["orphan"]
    assert out["SpaceHeatSystem"]["orphan"]["type"] == "WetDistribution"
