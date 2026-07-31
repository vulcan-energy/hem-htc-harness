# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
# Public API re-exports for backward compatibility.
# cli.py imports: find_summary_jsons, load_summary_series, PlotSettings,
#   SummarySeries, write_plotly_html_report, write_plotly_report_and_open
# test_utils.py imports: _daily_mean_series, _linear_fit_slope, _percentile

from .loader import (  # noqa: F401
    find_summary_jsons,
    load_summary_series,
)
from .models import (  # noqa: F401
    CalibrationData,
    PlotSettings,
    SummarySeries,
    VentilationSeries,
)
from .report import (  # noqa: F401
    build_plotly_figures,
    write_plotly_html_report,
    write_plotly_report_and_open,
)
from .utils import (  # noqa: F401
    _daily_mean_series,
    _linear_fit_slope,
    _percentile,
    plot_settings_from_series,
)
