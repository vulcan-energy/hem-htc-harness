# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
# Parsing helpers for results_heat_balance_air_node.csv.

from __future__ import annotations

import csv
from pathlib import Path

from .models import Timeseries
from .reader import parse_numeric_rows


def parse_heat_balance_air_node(path: Path) -> Timeseries:
    # Parse a heat-balance CSV (air-node/internal-boundary) into a timestep series.
    #
    # These CSVs include a short preamble and then a header row beginning with "Timestep".
    with path.open(newline="") as handle:
        reader = csv.reader(handle)
        rows = list(reader)

    if not rows:
        raise ValueError(f"Empty file: {path}")

    timestep_row_index: int | None = None
    for idx, row in enumerate(rows):
        if row and row[0] == "Timestep":
            timestep_row_index = idx
            break
    if timestep_row_index is None:
        raise ValueError(f"Could not find Timestep header row in {path}")

    header = rows[timestep_row_index]
    units = rows[timestep_row_index + 1] if timestep_row_index + 1 < len(rows) else []

    data_rows = [
        row
        for row in rows[timestep_row_index + 2 :]
        if row and not all(cell == "" for cell in row)
    ]
    return Timeseries(columns=header, units=units, rows=parse_numeric_rows(data_rows))
