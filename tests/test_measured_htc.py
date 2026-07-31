# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

import json
from pathlib import Path

import pytest

from htc_harness import read_measured_htc_json, validate_measured_htc_payload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _full_payload(**overrides) -> dict:
    """Return a valid full payload, with optional field overrides."""
    base = {
        "schema_version": "0.1",
        "htc_value_w_per_k": 210.5,
        "uncertainty_w_per_k": 12.0,
        "window": {
            "type": "index",
            "start_idx": 0,
            "end_idx": 17520,
            "timestep_hours": 0.5,
        },
        "method_profile": {
            "method_family": "dynamic_regression",
            "heat_input_proxy": "fuel_input",
            "temperature_definition": "sensor_avg",
            "storage_handling": "explicit_dTdt",
            "ventilation_treatment": "as_modelled",
            "windowing": "custom_window",
            "filters": "heating_on",
        },
        "conditions": {
            "occupancy_mode": "occupied_normal_use",
            "notes": "Test conditions.",
        },
        "source": "SMETER",
        "notes": "test",
    }
    base.update(overrides)
    return base


def _minimal_payload(**overrides) -> dict:
    """Return a valid minimal payload (only required fields + nulls)."""
    base = {
        "schema_version": "0.1",
        "htc_value_w_per_k": 150.0,
    }
    base.update(overrides)
    return base


def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "measured.json"
    p.write_text(json.dumps(payload))
    return p


# ---------------------------------------------------------------------------
# validate_measured_htc_payload – valid cases
# ---------------------------------------------------------------------------


class TestValidPayloads:
    def test_full_payload(self) -> None:
        assert validate_measured_htc_payload(_full_payload()) == []

    def test_minimal_payload(self) -> None:
        assert validate_measured_htc_payload(_minimal_payload()) == []

    def test_null_optional_fields(self) -> None:
        payload = _full_payload(
            uncertainty_w_per_k=None,
            window=None,
            method_profile=None,
            conditions=None,
            source=None,
            notes=None,
        )
        assert validate_measured_htc_payload(payload) == []

    def test_date_range_window(self) -> None:
        payload = _full_payload(
            window={
                "type": "date_range",
                "start": "2026-01-01T00:00:00",
                "end": "2026-02-01T00:00:00",
            }
        )
        assert validate_measured_htc_payload(payload) == []

    def test_conditions_minimal(self) -> None:
        payload = _full_payload(conditions={"occupancy_mode": "unoccupied_coheating"})
        assert validate_measured_htc_payload(payload) == []

    def test_all_occupancy_modes(self) -> None:
        for mode in ("unoccupied_coheating", "occupied_normal_use", "other"):
            payload = _full_payload(conditions={"occupancy_mode": mode})
            assert validate_measured_htc_payload(payload) == [], f"Failed for {mode}"


# ---------------------------------------------------------------------------
# validate_measured_htc_payload – invalid cases
# ---------------------------------------------------------------------------


class TestInvalidPayloads:
    def test_missing_schema_version(self) -> None:
        payload = _minimal_payload()
        del payload["schema_version"]
        errors = validate_measured_htc_payload(payload)
        assert any("schema_version" in e for e in errors)

    def test_wrong_schema_version(self) -> None:
        errors = validate_measured_htc_payload(_minimal_payload(schema_version="2.0"))
        assert any("Unsupported schema_version" in e for e in errors)

    def test_missing_htc_value(self) -> None:
        payload = {"schema_version": "0.1"}
        errors = validate_measured_htc_payload(payload)
        assert any("htc_value_w_per_k" in e for e in errors)

    def test_negative_htc_value(self) -> None:
        errors = validate_measured_htc_payload(_minimal_payload(htc_value_w_per_k=-5))
        assert any("must be > 0" in e for e in errors)

    def test_zero_htc_value(self) -> None:
        errors = validate_measured_htc_payload(_minimal_payload(htc_value_w_per_k=0))
        assert any("must be > 0" in e for e in errors)

    def test_htc_value_not_number(self) -> None:
        errors = validate_measured_htc_payload(
            _minimal_payload(htc_value_w_per_k="high")
        )
        assert any("must be a number" in e for e in errors)

    def test_negative_uncertainty(self) -> None:
        errors = validate_measured_htc_payload(_full_payload(uncertainty_w_per_k=-1))
        assert any("uncertainty" in e and ">= 0" in e for e in errors)

    def test_unknown_top_level_key(self) -> None:
        payload = _minimal_payload()
        payload["extra_field"] = True
        errors = validate_measured_htc_payload(payload)
        assert any("unknown keys" in e for e in errors)

    def test_window_missing_type(self) -> None:
        errors = validate_measured_htc_payload(_full_payload(window={"start_idx": 0}))
        assert any("type" in e for e in errors)

    def test_window_invalid_type(self) -> None:
        errors = validate_measured_htc_payload(_full_payload(window={"type": "bad"}))
        assert any("'index' or 'date_range'" in e for e in errors)

    def test_window_index_missing_keys(self) -> None:
        errors = validate_measured_htc_payload(_full_payload(window={"type": "index"}))
        assert len([e for e in errors if "window" in e]) >= 1

    def test_method_profile_missing_keys(self) -> None:
        errors = validate_measured_htc_payload(
            _full_payload(method_profile={"method_family": "x"})
        )
        assert any("method_profile missing keys" in e for e in errors)

    def test_conditions_missing_occupancy(self) -> None:
        errors = validate_measured_htc_payload(
            _full_payload(conditions={"notes": "no mode"})
        )
        assert any("occupancy_mode" in e for e in errors)

    def test_conditions_invalid_occupancy(self) -> None:
        errors = validate_measured_htc_payload(
            _full_payload(conditions={"occupancy_mode": "magic"})
        )
        assert any("occupancy_mode" in e for e in errors)

    def test_source_must_be_string_or_null(self) -> None:
        errors = validate_measured_htc_payload(_full_payload(source=123))
        assert any("source" in e and "string" in e for e in errors)

    def test_notes_must_be_string_or_null(self) -> None:
        errors = validate_measured_htc_payload(_full_payload(notes=42))
        assert any("notes must be a string" in e for e in errors)

    def test_unknown_key_in_method_profile(self) -> None:
        mp = _full_payload()["method_profile"].copy()
        mp["extra"] = "oops"
        errors = validate_measured_htc_payload(_full_payload(method_profile=mp))
        assert any("method_profile" in e and "unknown" in e for e in errors)

    def test_unknown_key_in_conditions(self) -> None:
        errors = validate_measured_htc_payload(
            _full_payload(conditions={"occupancy_mode": "other", "bogus": True})
        )
        assert any("conditions" in e and "unknown" in e for e in errors)

    def test_unknown_key_in_window_index(self) -> None:
        w = _full_payload()["window"].copy()
        w["extra"] = 1
        errors = validate_measured_htc_payload(_full_payload(window=w))
        assert any("window" in e and "unknown" in e for e in errors)

    def test_unknown_key_in_window_date_range(self) -> None:
        w = {
            "type": "date_range",
            "start": "2026-01-01T00:00:00",
            "end": "2026-02-01T00:00:00",
            "extra": True,
        }
        errors = validate_measured_htc_payload(_full_payload(window=w))
        assert any("window" in e and "unknown" in e for e in errors)

    def test_conditions_notes_must_be_string(self) -> None:
        errors = validate_measured_htc_payload(
            _full_payload(conditions={"occupancy_mode": "other", "notes": 99})
        )
        assert any("conditions.notes" in e for e in errors)

    def test_method_profile_values_must_be_strings(self) -> None:
        mp = _full_payload()["method_profile"].copy()
        mp["filters"] = 123
        errors = validate_measured_htc_payload(_full_payload(method_profile=mp))
        assert any("method_profile.filters" in e and "string" in e for e in errors)


# ---------------------------------------------------------------------------
# read_measured_htc_json – integration
# ---------------------------------------------------------------------------


class TestReadMeasuredHtcJson:
    def test_full_file(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _full_payload())
        m = read_measured_htc_json(path)
        assert m.htc_value_w_per_k == 210.5
        assert m.uncertainty_w_per_k == 12.0
        assert m.method_profile is not None
        assert m.method_profile.method_family == "dynamic_regression"
        assert m.conditions is not None
        assert m.conditions.occupancy_mode == "occupied_normal_use"
        assert m.conditions.notes == "Test conditions."
        assert m.source == "SMETER"
        assert m.notes == "test"

    def test_minimal_file(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _minimal_payload())
        m = read_measured_htc_json(path)
        assert m.htc_value_w_per_k == 150.0
        assert m.uncertainty_w_per_k is None
        assert m.method_profile is None
        assert m.conditions is None
        assert m.source is None
        assert m.notes is None

    def test_rejects_missing_schema_version(self, tmp_path: Path) -> None:
        payload = _minimal_payload()
        del payload["schema_version"]
        path = _write(tmp_path, payload)
        with pytest.raises(ValueError, match="schema_version"):
            read_measured_htc_json(path)

    def test_rejects_invalid_htc(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _minimal_payload(htc_value_w_per_k=-1))
        with pytest.raises(ValueError, match="must be > 0"):
            read_measured_htc_json(path)

    def test_rejects_partial_method_profile(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            _full_payload(method_profile={"method_family": "dynamic_regression"}),
        )
        with pytest.raises(ValueError, match="method_profile missing"):
            read_measured_htc_json(path)
