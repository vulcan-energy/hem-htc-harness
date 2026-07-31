# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
# htc-harness: Minimal empirical HTC wrapper for HEM outputs.

from .empirical import (
    analyze_offsets,
    compute_design_htc,
)
from .models import (
    MeasuredConditions,
    MeasuredHTC,
    MethodProfile,
    RunFiles,
    Timeseries,
)
from .parse_heat_balance import parse_heat_balance_air_node
from .reader import (
    locate_run_files,
    read_csv_timeseries,
    read_measured_htc_json,
    read_results_static,
    validate_measured_htc_payload,
)
from .runner import HemRunRequest, build_command, run_hem

__all__ = [
    "MeasuredConditions",
    "MeasuredHTC",
    "MethodProfile",
    "RunFiles",
    "Timeseries",
    "HemRunRequest",
    "build_command",
    "run_hem",
    "locate_run_files",
    "read_csv_timeseries",
    "read_measured_htc_json",
    "read_results_static",
    "validate_measured_htc_payload",
    "analyze_offsets",
    "parse_heat_balance_air_node",
    "compute_design_htc",
]
__version__ = "0.1.0"
