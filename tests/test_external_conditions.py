# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

import json

import pytest

from htc_harness.reader import (
    merge_external_conditions_into_model,
    offset_external_conditions_air_temperatures,
    read_external_conditions_file,
)


def test_read_external_conditions_file_accepts_wrapper(tmp_path) -> None:
    path = tmp_path / "ec.json"
    payload = {
        "_metadata": {"x": 1},
        "ExternalConditions": {"air_temperatures": [1, 2]},
    }
    path.write_text(json.dumps(payload))
    ec = read_external_conditions_file(path)
    assert ec["air_temperatures"] == [1, 2]


def test_offset_external_conditions_shifts_temperatures() -> None:
    ec = {"air_temperatures": [10.0, 10.5]}
    shifted = offset_external_conditions_air_temperatures(ec, 0.5)
    assert shifted["air_temperatures"] == [10.5, 11.0]


def test_merge_preserves_base_shading_segments() -> None:
    base = {
        "ExternalConditions": {
            "shading_segments": [
                {
                    "number": 1,
                    "start360": 0,
                    "end360": 10,
                    "shading": [{"type": "obstacle", "height": 1.0, "distance": 2.0}],
                }
            ]
        }
    }
    incoming = {
        "air_temperatures": [1.0, 2.0],
        "shading_segments": [{"number": 1, "start360": 0, "end360": 10, "shading": []}],
    }
    merged = merge_external_conditions_into_model(
        model=base, external_conditions=incoming, preserve_shading_segments=True
    )
    assert merged["ExternalConditions"]["air_temperatures"] == [1.0, 2.0]
    assert (
        merged["ExternalConditions"]["shading_segments"][0]["shading"][0]["height"]
        == 1.0
    )


def test_offset_then_merge_preserves_shading(tmp_path) -> None:
    base = {
        "ExternalConditions": {
            "shading_segments": [{"number": 1, "start360": 0, "end360": 10}]
        }
    }
    incoming = {
        "air_temperatures": [10, 11],
        "shading_segments": [{"number": 999, "start360": 0, "end360": 10}],
    }
    shifted = offset_external_conditions_air_temperatures(incoming, -0.5)
    merged = merge_external_conditions_into_model(
        model=base, external_conditions=shifted, preserve_shading_segments=True
    )
    assert merged["ExternalConditions"]["air_temperatures"] == [9.5, 10.5]
    assert merged["ExternalConditions"]["shading_segments"][0]["number"] == 1


def test_offset_external_conditions_requires_air_temperatures() -> None:
    with pytest.raises(ValueError):
        offset_external_conditions_air_temperatures({}, 0.5)
