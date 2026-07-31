# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
# Lightweight data models for HTC harness core logic.

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MethodProfile:
    # Method profile labels boundary conditions for HTC comparisons.

    method_family: str
    heat_input_proxy: str
    temperature_definition: str
    storage_handling: str
    ventilation_treatment: str
    windowing: str
    filters: str


@dataclass(frozen=True)
class Timeseries:
    # Generic timeseries container with header, units, and numeric rows.

    columns: Sequence[str]
    units: Sequence[str]
    rows: Sequence[Sequence[float | None]]

    def column_index(self, name: str) -> int:
        # Return the index for a column name or raise a clear error.
        try:
            return self.columns.index(name)
        except ValueError as exc:
            raise ValueError(f"Column not found: {name}") from exc

    def get_column(self, name: str) -> list[float | None]:
        # Return a column by name as a list.
        idx = self.column_index(name)
        return [row[idx] for row in self.rows]


@dataclass(frozen=True)
class RunFiles:
    # Resolved paths to required HEM output files for a run.

    results_csv: Path | None
    heat_balance_air_node_csv: Path
    heat_balance_external_boundary_csv: Path | None
    heat_balance_internal_boundary_csv: Path | None
    results_static_csv: Path


@dataclass(frozen=True)
class MeasuredConditions:
    # Conditions under which the HTC was measured.

    occupancy_mode: str  # "unoccupied_coheating" | "occupied_normal_use" | "other"
    notes: str | None = None


@dataclass(frozen=True)
class MeasuredHTC:
    # Measured HTC input with optional method metadata.

    htc_value_w_per_k: float
    method_profile: MethodProfile | None
    window: object | None
    uncertainty_w_per_k: float | None = None
    conditions: MeasuredConditions | None = None
    source: str | None = None
    notes: str | None = None
