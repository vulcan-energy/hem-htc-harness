# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest


def _elements(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return model["Zone"]["Zone 1"]["BuildingElement"]


def _walk_orientations(value: Any) -> Iterator[float]:
    if isinstance(value, dict):
        orientation = value.get("orientation360")
        if isinstance(orientation, (int, float)):
            yield float(orientation)
        for child in value.values():
            yield from _walk_orientations(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_orientations(child)


def _wall_orientations(model: dict[str, Any]) -> set[float]:
    return {
        float(element["orientation360"])
        for element in _elements(model).values()
        if element.get("type") == "BuildingElementOpaque"
        and element.get("pitch") == 90
        and not element.get("is_external_door")
    }


def _horizontal_types(model: dict[str, Any]) -> set[tuple[str, float | None]]:
    return {
        (element["type"], element.get("pitch"))
        for element in _elements(model).values()
        if element.get("pitch") in (0, 180)
        or element.get("type") == "BuildingElementGround"
    }


def test_sandbox_configuration_is_strict_and_separate_from_numeric_levers() -> None:
    from htc_harness import browser
    from htc_harness.errors import HarnessError

    values = {
        "dwellingForm": "flat",
        "flatFloor": "mid",
        "flatAspect": "single",
        "orientation": 45,
        "wallU": 0.25,
    }

    config = browser.sandbox_configuration_from_values(values)
    assert config == browser.SandboxConfiguration(
        dwelling_form="flat",
        flat_floor="mid",
        flat_aspect="single",
        orientation_degrees=45,
    )
    assert browser.levers_from_attributes(values).wall_u_value == 0.25

    with pytest.raises(HarnessError, match="dwellingForm"):
        browser.sandbox_configuration_from_values({"dwellingForm": "bungalow"})
    with pytest.raises(HarnessError, match="flatFloor"):
        browser.sandbox_configuration_from_values({"flatFloor": "basement"})
    with pytest.raises(HarnessError, match="flatAspect"):
        browser.sandbox_configuration_from_values({"flatAspect": "triple"})
    with pytest.raises(HarnessError, match="orientation"):
        browser.sandbox_configuration_from_values({"orientation": 20})


@pytest.mark.parametrize(
    ("dwelling_form", "expected_walls", "expected_party_count"),
    [
        ("detached", {0.0, 90.0, 180.0, 270.0}, 0),
        ("end-terrace", {0.0, 180.0, 270.0}, 2),
        ("mid-terrace", {0.0, 180.0}, 4),
    ],
)
def test_house_forms_change_the_exposed_and_party_wall_topology(
    dwelling_form: str,
    expected_walls: set[float],
    expected_party_count: int,
) -> None:
    from htc_harness import browser

    model = browser.sandbox_model_for_configuration(
        browser.SandboxConfiguration(dwelling_form=dwelling_form)
    )
    elements = _elements(model)

    assert model["General"]["build_type"] == "house"
    assert _wall_orientations(model) == expected_walls
    assert (
        sum(
            element.get("type") == "BuildingElementPartyWall"
            for element in elements.values()
        )
        == expected_party_count
    )
    if dwelling_form == "mid-terrace":
        assert not any(
            element.get("type") == "BuildingElementTransparent"
            and element.get("orientation360") == 270
            for element in elements.values()
        )


@pytest.mark.parametrize(
    "aspect, expected_walls, party_count",
    [
        ("single", {180.0}, 3),
        ("dual", {180.0, 270.0}, 2),
    ],
)
def test_flat_aspect_controls_how_many_facades_are_exposed(
    aspect: str, expected_walls: set[float], party_count: int
) -> None:
    from htc_harness import browser

    model = browser.sandbox_model_for_configuration(
        browser.SandboxConfiguration(
            dwelling_form="flat", flat_floor="mid", flat_aspect=aspect
        )
    )
    elements = _elements(model)

    assert model["General"] == {
        "build_type": "flat",
        "storeys_in_dwelling": 1,
        "storey_of_dwelling": 2,
        "storeys_in_building": 3,
    }
    assert _wall_orientations(model) == expected_walls
    assert (
        sum(
            element.get("type") == "BuildingElementPartyWall"
            for element in elements.values()
        )
        == party_count
    )


@pytest.mark.parametrize(
    ("floor", "storey", "required", "forbidden"),
    [
        (
            "ground",
            1,
            {
                ("BuildingElementGround", None),
                ("BuildingElementAdjacentConditionedSpace", 180),
            },
            {
                ("BuildingElementAdjacentConditionedSpace", 0),
                ("BuildingElementOpaque", 0),
            },
        ),
        (
            "mid",
            2,
            {
                ("BuildingElementAdjacentConditionedSpace", 0),
                ("BuildingElementAdjacentConditionedSpace", 180),
            },
            {("BuildingElementGround", None), ("BuildingElementOpaque", 0)},
        ),
        (
            "top",
            3,
            {
                ("BuildingElementAdjacentConditionedSpace", 0),
                ("BuildingElementOpaque", 0),
            },
            {
                ("BuildingElementGround", None),
                ("BuildingElementAdjacentConditionedSpace", 180),
            },
        ),
    ],
)
def test_flat_floor_position_controls_the_horizontal_envelope(
    floor: str,
    storey: int,
    required: set[tuple[str, float | None]],
    forbidden: set[tuple[str, float | None]],
) -> None:
    from htc_harness import browser

    model = browser.sandbox_model_for_configuration(
        browser.SandboxConfiguration(
            dwelling_form="flat", flat_floor=floor, flat_aspect="single"
        )
    )

    assert model["General"]["storey_of_dwelling"] == storey
    horizontal = _horizontal_types(model)
    assert required <= horizontal
    assert horizontal.isdisjoint(forbidden)
    if floor == "top":
        roof = next(
            element
            for element in _elements(model).values()
            if element.get("type") == "BuildingElementOpaque"
            and element.get("pitch") == 0
        )
        assert "orientation360" not in roof


def test_orientation_rotates_every_oriented_input_from_the_front_face() -> None:
    from htc_harness import browser

    south = browser.sandbox_model_for_configuration(
        browser.SandboxConfiguration(dwelling_form="detached", orientation_degrees=180)
    )
    north_east = browser.sandbox_model_for_configuration(
        browser.SandboxConfiguration(dwelling_form="detached", orientation_degrees=45)
    )

    south_values = list(_walk_orientations(south))
    rotated_values = list(_walk_orientations(north_east))
    assert len(rotated_values) == len(south_values) > 4
    assert rotated_values == [
        pytest.approx((value - 135) % 360) for value in south_values
    ]
    assert 45 in _wall_orientations(north_east)


@pytest.mark.parametrize(
    "config",
    [
        pytest.param({"dwellingForm": "detached"}, id="detached"),
        pytest.param({"dwellingForm": "end-terrace"}, id="end-terrace"),
        pytest.param({"dwellingForm": "mid-terrace"}, id="mid-terrace"),
        pytest.param(
            {"dwellingForm": "flat", "flatFloor": "ground", "flatAspect": "single"},
            id="ground-single-flat",
        ),
        pytest.param(
            {"dwellingForm": "flat", "flatFloor": "mid", "flatAspect": "dual"},
            id="mid-dual-flat",
        ),
        pytest.param(
            {"dwellingForm": "flat", "flatFloor": "top", "flatAspect": "single"},
            id="top-single-flat",
        ),
    ],
)
def test_staged_forms_keep_floor_area_and_leak_envelope_consistent(
    config: dict[str, Any], tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from htc_harness import browser

    monkeypatch.setattr(browser, "WORK_ROOT", tmp_path)
    staged = browser._stage_sandbox_run({**config, "floorArea": 71.2}, "form")
    model = json.loads(staged["model_path"].read_text())

    assert staged["floor_area_m2"] == pytest.approx(71.2)
    assert browser.total_floor_area(model) == pytest.approx(71.2)
    assert model["InfiltrationVentilation"]["Leaks"]["env_area"] == pytest.approx(
        browser.sandbox_envelope_area(model)
    )
