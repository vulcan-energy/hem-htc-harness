# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
"""h3 must be able to read the output of the engine it pins.

The engine re-pin (0.36.0-alpha -> 1.0.0-alpha-7) renamed every heat-balance
column, which left h3 unable to analyse the very engine it builds. Patched-vs-
unpatched equivalence testing did not catch it, because that only compares
engine output against engine output and never runs h3's reader.

These tests close that gap: they run the real analysis against recorded output
from the pinned engine.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from htc_harness import empirical as E
from htc_harness.empirical import analyze_offsets
from htc_harness.parse_heat_balance import parse_heat_balance_air_node

FIXTURES = Path(__file__).parent / "fixtures" / "hem_output"
AIR_NODE = FIXTURES / "model__core__results_heat_balance_air_node.csv"


def test_air_node_loss_columns_are_found_in_pinned_engine_output() -> None:
    """The loss suffixes must match the engine's per-timestep column names.

    Note the CSV holds two sections: annual totals prefixed 'total ', and the
    per-timestep block below. Matching the 'total ' names finds nothing.
    """
    timeseries = parse_heat_balance_air_node(AIR_NODE)
    matched = [
        name
        for name in timeseries.columns
        if ":" in name and name.endswith(E.AIR_NODE_LOSS_SUFFIXES)
    ]
    assert matched, (
        "no air-node loss columns matched; h3 cannot read the pinned engine. "
        f"Columns were: {timeseries.columns}"
    )
    # Two zones x three loss terms in this fixture.
    assert len(matched) == 6


@pytest.mark.parametrize(
    "suffixes",
    [
        E.AIR_NODE_LOSS_SUFFIXES,
        E.SYSTEM_GAINS_SUFFIX,
        E.AIR_NODE_SOLAR_GAINS_SUFFIX,
        E.AIR_NODE_INTERNAL_GAINS_SUFFIX,
        E.AIR_NODE_STORAGE_SUFFIX,
    ],
)
def test_every_air_node_suffix_matches_something(suffixes: tuple[str, ...]) -> None:
    timeseries = parse_heat_balance_air_node(AIR_NODE)
    assert any(
        ":" in name and name.endswith(suffixes) for name in timeseries.columns
    ), f"no column matched {suffixes}"


def _recorded_run_dirs(tmp_path: Path, *, skip: str | None = None) -> dict[float, Path]:
    results_dirs: dict[float, Path] = {}
    for offset in (0.0, -1.0, 1.0):
        run_dir = tmp_path / f"offset{offset}" / "model__results"
        run_dir.mkdir(parents=True)
        for csv_file in FIXTURES.iterdir():
            if skip is not None and csv_file.name == skip:
                continue
            shutil.copy(csv_file, run_dir / csv_file.name)
        results_dirs[offset] = run_dir
    return results_dirs


def test_analysis_completes_against_pinned_engine_output(tmp_path: Path) -> None:
    """End-to-end: recorded engine output must flow through analyze_offsets."""
    payload = analyze_offsets(
        results_dirs_by_offset_k=_recorded_run_dirs(tmp_path),
        timestep_hours=0.5,
        start_idx=None,
        end_idx=None,
        heating_only=False,
        measured_htc_path=None,
        include_fihc=True,
    )
    assert "job" in payload


def test_analysis_completes_without_results_csv(tmp_path: Path) -> None:
    """results.csv is optional; `locate_run_files` returns None rather than raising.

    The alignment bound used by the per-offset loop was computed inside the
    results.csv branch, so analysing a run without one raised UnboundLocalError
    from deep inside the analysis instead of working.
    """
    payload = analyze_offsets(
        results_dirs_by_offset_k=_recorded_run_dirs(
            tmp_path, skip="model__core__results.csv"
        ),
        timestep_hours=0.5,
        heating_only=False,
        include_fihc=True,
    )

    assert isinstance(payload["summary"]["htc_empirical_w_per_k"], float)
    # Series sourced from results.csv are simply absent, not fabricated.
    assert payload["derived"]["base_results_series"] == {}


def test_epw_weather_series_survives_a_non_utf8_header(tmp_path: Path) -> None:
    """Real EPW headers carry station names in the local encoding.

    These series are diagnostic — a missing one must not fail the run — but a
    Latin-1 station name raised UnicodeDecodeError out of the reader, aborting
    the whole analysis.
    """
    from htc_harness.empirical import _external_series_from_epw

    epw = tmp_path / "station.epw"
    header = ["LOCATION,Mu\xf1oz,ES"] + ["hdr"] * 7
    rows = [",".join(["0"] * 6 + ["10.5"] + ["0"] * 13 + ["3.2"] + ["0"] * 6)] * 4
    epw.write_bytes("\n".join(header + rows).encode("latin-1"))

    assert _external_series_from_epw(epw, 6, 4) == [10.5] * 4
    assert _external_series_from_epw(epw, 20, 4) == [3.2] * 4
