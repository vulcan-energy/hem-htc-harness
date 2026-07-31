# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Sequence
from pathlib import Path

# The report and the summary must agree on the numbers they show, so the
# analysis primitives come from `empirical` rather than being restated here.
# `_block_htc_from_summary` keeps its historical local name to leave the call
# sites in `figures.py` readable.
from ..empirical import (
    _bin_ranges,
    _daily_means,
    _get_offset_series,
    _linear_fit_slope_intercept,
    _mask_from_summary,
    _mean,
    _percentile,
    _valid_fraction,
)
from ..empirical import _block_htc_from_summary as _block_htc
from .models import PlotSettings, SummarySeries

PLOT_FONT = "'Clash Grotesk', 'Inter', system-ui, -apple-system, sans-serif"
PAPER_BG = "#F7F9F8"
PLOT_BG = "#FFFFFF"
TEXT_COLOR = "#1A3437"
GRID_COLOR = "rgba(26, 52, 55, 0.05)"
AXIS_COLOR = "rgba(26, 52, 55, 0.25)"
COLORWAY = ["#1A3437", "#2A4A4F", "#7C9A92", "#B7CEC4", "#EAFD5A", "#FF6B6B"]
THEME_ACCENT = "#EAFD5A"
COMPONENT_COLORS = {
    "FabricHeatLoss": "#F28E2B",
    "InfiltrationVentilation": "#4E79A7",
    "ThermalBridges": "#59A14F",
}

# Human-readable labels for component keys.
# FabricHeatLoss now includes the radiative heating component (FabricIntHeatCool)
# folded in from the internal-boundary heat balance, so we label it clearly.
COMPONENT_LABELS = {
    "FabricHeatLoss": "Fabric (conv. + rad. heating)",
    "InfiltrationVentilation": "Infiltration & Ventilation",
    "ThermalBridges": "Thermal Bridges",
}


def _comp_label(key: str) -> str:
    """Return a human-readable label for a component data key."""
    return COMPONENT_LABELS.get(key, key)


def _plotly_imports():
    # Lazy import for optional plotly dependency.
    try:
        import plotly.graph_objects as go  # type: ignore
        import plotly.io as pio  # type: ignore
        from plotly.subplots import make_subplots  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "plotly is required for plotting. Install with: pip install plotly"
        ) from exc
    return go, pio, make_subplots


def _subplot_title(title: str) -> str:
    return f"<b>{title}</b>"


def _base_layout(fig, title: str) -> None:
    meta = dict(fig.layout.meta) if isinstance(fig.layout.meta, dict) else {}
    meta["title"] = title
    fig.update_layout(meta=meta)
    fig.update_layout(
        height=700,
        title_text="",
        font=dict(family=PLOT_FONT, color=TEXT_COLOR, size=12),
        paper_bgcolor=PAPER_BG,
        plot_bgcolor=PLOT_BG,
        colorway=COLORWAY,
        legend=dict(
            orientation="v",
            x=1.02,
            y=1,
            xanchor="left",
            yanchor="top",
            font=dict(size=10),
            groupclick="toggleitem",
            itemclick=False,
            itemdoubleclick=False,
        ),
        margin=dict(l=55, r=200, t=80, b=70),
        hovermode="x unified",
    )
    fig.update_xaxes(
        showgrid=True, gridcolor=GRID_COLOR, zeroline=False, linecolor=AXIS_COLOR
    )
    fig.update_yaxes(
        showgrid=True, gridcolor=GRID_COLOR, zeroline=False, linecolor=AXIS_COLOR
    )


def _bin_hours_label(hours: float) -> str:
    label_map = {
        24.0: "Daily",
        168.0: "Weekly",
        720.0: "Monthly",
    }
    return label_map.get(hours, f"{hours:g}h")


def _plot_agg_hours(series: SummarySeries, settings: PlotSettings) -> list[float]:
    if settings.agg_hours:
        return list(settings.agg_hours)
    return [24.0, 168.0, 720.0]


def _month_ranges(series: SummarySeries) -> list[tuple[int, int, str]]:
    ranges: list[tuple[int, int, str]] = []
    for item in series.month_bins:
        start = item.get("start")
        end = item.get("end")
        label = item.get("label")
        if isinstance(start, int) and isinstance(end, int):
            ranges.append((start, end, str(label) if label is not None else ""))
    return ranges


def _ranges_for_hours(
    series: SummarySeries, hours: float
) -> tuple[list[tuple[int, int]], list[str], bool]:
    if hours == 720.0 and series.month_bins:
        month_ranges = _month_ranges(series)
        return (
            [(start, end) for start, end, _ in month_ranges],
            [label for _, _, label in month_ranges],
            True,
        )
    steps = int(round(hours / series.timestep_hours))
    if steps <= 0 or abs((steps * series.timestep_hours) - hours) > 1e-9:
        return [], [], False
    if (
        series.selected_indices
        and series.analysis_window_start is not None
        and series.analysis_window_end is not None
        and series.analysis_window_end > series.analysis_window_start
    ):
        window_start = int(series.analysis_window_start)
        window_end = int(series.analysis_window_end)
        n_full = max(0, window_end - window_start)
        full_ranges = _bin_ranges(n_full, steps)
        selected_rel = [
            i - window_start
            for i in series.selected_indices
            if window_start <= i < window_end
        ]
        mapped_ranges: list[tuple[int, int]] = []
        for full_start, full_end in full_ranges:
            start_pos = bisect_left(selected_rel, full_start)
            end_pos = bisect_left(selected_rel, full_end)
            mapped_ranges.append((start_pos, end_pos))
        return mapped_ranges, [], False
    n = len(_get_offset_series(series.q_loss_by_offset, 0.0) or [])
    return _bin_ranges(n, steps), [], False


_RANGES_CACHE: dict[
    tuple[Path, float], tuple[list[tuple[int, int]], list[str], bool]
] = {}


def _cached_ranges(
    series: SummarySeries, hours: float
) -> tuple[list[tuple[int, int]], list[str], bool]:
    cache_key = (series.path, hours)
    cached = _RANGES_CACHE.get(cache_key)
    if cached is not None:
        return cached
    computed = _ranges_for_hours(series, hours)
    _RANGES_CACHE[cache_key] = computed
    return computed


def _mask_inputs(
    series: SummarySeries, settings: PlotSettings
) -> tuple[list[bool] | None, str]:
    """Build the per-timestep mask, then gate it at block granularity.

    The per-timestep half is `empirical._mask_from_summary`, so the report
    masks exactly the timesteps the summary does. The gating half is *not*
    `empirical._apply_block_gating`: that bins uniformly over the selected
    array, while the report bins on calendar boundaries mapped through
    `selected_indices`. The two agree whenever nothing was filtered out, and
    differ under `--heating-only` or a sparse window.
    """
    mask, reason = _mask_from_summary(
        q_system_total_by_offset=series.q_system_total_by_offset,
        base_results_series=series.base_results_series,
        timestep_hours=series.timestep_hours,
        heating_eps_w=settings.heating_eps_w,
        unmet_eps_kwh=settings.unmet_eps_kwh,
    )
    if mask is None:
        return None, reason

    if settings.mask_block_hours is None:
        return mask, ""
    if settings.valid_min_fraction is None:
        return mask, ""
    block_hours = float(settings.mask_block_hours)
    if block_hours <= 0:
        return mask, ""

    ranges, _, _ = _cached_ranges(series, block_hours)
    if not ranges:
        return mask, ""
    fractions = _valid_fraction(mask, ranges)
    keep_blocks = [f >= settings.valid_min_fraction for f in fractions]
    gated = mask[:]
    for (start, end), keep in zip(ranges, keep_blocks):
        if keep:
            continue
        for i in range(start, min(end, len(gated))):
            gated[i] = False
    return gated, ""


def _weekly_masked_component_summary(
    series: SummarySeries, settings: PlotSettings
) -> dict[str, object]:
    """
    Compute weekly masked+filtered HTC totals and component means.

    This summary is primarily used to derive calibration component shares:
    - compute per-week regression fits (masked)
    - keep a week only if valid_fraction >= settings.valid_min_fraction
    - return mean of kept weekly HTCs and component means

    """

    series_label = series.path.parent.name
    mask, mask_error = _mask_inputs(series, settings)
    if mask is None:
        return {
            "series": series_label,
            "weekly_masked_mean_w_per_k": None,
            "weekly_fabric_w_per_k": None,
            "weekly_bridges_w_per_k": None,
            "weekly_vent_w_per_k": None,
            "weekly_kept": 0,
            "weekly_total": 0,
            "weekly_valid_min_fraction": settings.valid_min_fraction,
            "design_w_per_k": series.htc_design,
            "mask_error": mask_error,
        }

    ranges, _, _ = _cached_ranges(series, 168.0)
    if not ranges:
        return {
            "series": series_label,
            "weekly_masked_mean_w_per_k": None,
            "weekly_fabric_w_per_k": None,
            "weekly_bridges_w_per_k": None,
            "weekly_vent_w_per_k": None,
            "weekly_kept": 0,
            "weekly_total": 0,
            "weekly_valid_min_fraction": settings.valid_min_fraction,
            "design_w_per_k": series.htc_design,
            "mask_error": "Missing weekly ranges",
        }

    weekly_masked = _block_htc(
        series.offsets_k,
        series.q_loss_by_offset,
        ranges,
        series.timestep_hours,
        mask,
    )
    kept = _keep_blocks(mask, ranges, settings)
    weekly_kept_vals = [
        h for h, k in zip(weekly_masked, kept) if k and math.isfinite(h)
    ]
    weekly_mean = _mean(weekly_kept_vals) if weekly_kept_vals else None

    # Headline-aligned component breakdown (weekly mean), if components are available.
    component_means: dict[str, float | None] = {
        "fabric": None,
        "bridges": None,
        "vent": None,
    }
    try:
        comp_names = [
            ("FabricHeatLoss", "fabric"),
            ("ThermalBridges", "bridges"),
            ("InfiltrationVentilation", "vent"),
        ]
        comp_weekly: dict[str, list[float]] = {}
        for comp_name, comp_key in comp_names:
            comp_series_by_offset: dict[str, list[float]] = {}
            for k, payload in series.q_loss_components_by_offset.items():
                if comp_name in payload:
                    comp_series_by_offset[k] = payload[comp_name]
            if not comp_series_by_offset:
                comp_weekly = {}
                break
            comp_weekly[comp_key] = _block_htc(
                series.offsets_k,
                comp_series_by_offset,
                ranges,
                series.timestep_hours,
                mask,
            )

        if comp_weekly and all(
            key in comp_weekly for key in ("fabric", "bridges", "vent")
        ):
            fabric_scaled: list[float] = []
            bridges_scaled: list[float] = []
            vent_scaled: list[float] = []
            for i, keep in enumerate(kept):
                if not keep:
                    continue
                h_total = weekly_masked[i] if i < len(weekly_masked) else float("nan")
                h_f = (
                    comp_weekly["fabric"][i]
                    if i < len(comp_weekly["fabric"])
                    else float("nan")
                )
                h_b = (
                    comp_weekly["bridges"][i]
                    if i < len(comp_weekly["bridges"])
                    else float("nan")
                )
                h_v = (
                    comp_weekly["vent"][i]
                    if i < len(comp_weekly["vent"])
                    else float("nan")
                )
                if not (
                    math.isfinite(h_total)
                    and math.isfinite(h_f)
                    and math.isfinite(h_b)
                    and math.isfinite(h_v)
                ):
                    continue
                h_total = max(h_total, 0.0)
                h_f = max(h_f, 0.0)
                h_b = max(h_b, 0.0)
                h_v = max(h_v, 0.0)
                denom = h_f + h_b + h_v
                if denom <= 0:
                    continue
                scale = h_total / denom
                fabric_scaled.append(h_f * scale)
                bridges_scaled.append(h_b * scale)
                vent_scaled.append(h_v * scale)
            component_means = {
                "fabric": _mean(fabric_scaled) if fabric_scaled else None,
                "bridges": _mean(bridges_scaled) if bridges_scaled else None,
                "vent": _mean(vent_scaled) if vent_scaled else None,
            }
    except Exception:
        component_means = {"fabric": None, "bridges": None, "vent": None}

    return {
        "series": series_label,
        "weekly_masked_mean_w_per_k": weekly_mean,
        "weekly_fabric_w_per_k": component_means["fabric"],
        "weekly_bridges_w_per_k": component_means["bridges"],
        "weekly_vent_w_per_k": component_means["vent"],
        "weekly_kept": sum(1 for k in kept if k),
        "weekly_total": len(kept),
        "weekly_valid_min_fraction": settings.valid_min_fraction,
        "design_w_per_k": series.htc_design,
        "mask_error": "",
    }


def _keep_blocks(
    mask: list[bool],
    ranges: Sequence[tuple[int, int]],
    settings: PlotSettings,
) -> list[bool]:
    """Return a boolean per block: True if block passes the valid-fraction filter."""
    valid_frac = _valid_fraction(mask, ranges)
    if settings.valid_min_fraction is not None:
        return [f >= settings.valid_min_fraction for f in valid_frac]
    return [True for _ in valid_frac]


def _apply_y_range(
    fig,
    values: list[float],
    agg_hours,
    *,
    row: int = 1,
    pct: tuple[float, float] | None = None,
    secondary_y: bool = False,
) -> None:
    """Apply a padded y-axis range to all subplot columns for a given row."""
    if not values:
        return
    if pct is not None:
        y_min = _percentile(values, pct[0])
        y_max = _percentile(values, pct[1])
    else:
        y_min = min(values)
        y_max = max(values)
    pad = 0.05 * (y_max - y_min) if y_max > y_min else 1.0
    kwargs: dict = {"range": [y_min - pad, y_max + pad], "row": row}
    if secondary_y:
        kwargs["secondary_y"] = True
    for col in range(1, len(agg_hours) + 1):
        fig.update_yaxes(col=col, **kwargs)


def _linear_fit_slope(x: Sequence[float], y: Sequence[float]) -> float:
    m, _ = _linear_fit_slope_intercept(x, y)
    return m


def _daily_mean_series(
    values: Sequence[float], *, timestep_hours: float
) -> tuple[list[int], list[float]]:
    means = _daily_means(values, timestep_hours)
    xs = list(
        range(
            0,
            len(means) * int(round(24.0 / timestep_hours)),
            int(round(24.0 / timestep_hours)),
        )
    )
    return xs, means


def _annotate_mask_error(fig, mask_error: str, agg_hours) -> None:
    """Add mask-error text to every subplot column."""
    for col in range(1, len(agg_hours) + 1):
        fig.add_annotation(
            text=mask_error,
            x=0.5,
            y=0.5,
            xref="x domain",
            yref="y domain",
            showarrow=False,
            font=dict(size=11, color=TEXT_COLOR),
            row=1,
            col=col,
        )


def _mask_error_figure(fig, mask_error: str, agg_hours, title: str):
    """Fill every subplot with a mask-error annotation and return the styled figure."""
    _annotate_mask_error(fig, mask_error, agg_hours)
    _base_layout(fig, title)
    return fig


def _make_agg_subplots(
    series: SummarySeries,
    settings: PlotSettings,
    make_subplots,
    *,
    rows: int = 1,
    row_heights: list[float] | None = None,
    vertical_spacing: float | None = None,
):
    """Build a subplot figure with one column per aggregation level."""
    agg_hours = _plot_agg_hours(series, settings)
    subplot_titles = [_subplot_title(_bin_hours_label(hours)) for hours in agg_hours]
    kwargs: dict = {
        "rows": rows,
        "cols": len(agg_hours),
        "subplot_titles": tuple(subplot_titles),
    }
    if row_heights is not None:
        kwargs["row_heights"] = row_heights
    if vertical_spacing is not None:
        kwargs["vertical_spacing"] = vertical_spacing
    fig = make_subplots(**kwargs)
    return fig, agg_hours


def _find_results_static(base_results_dir: Path) -> Path | None:
    patterns = ("*__results_static.csv", "results_static.csv")
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(sorted(base_results_dir.glob(pattern)))
    return matches[0] if matches else None


def plot_settings_from_series(series: SummarySeries) -> PlotSettings:
    """Build PlotSettings that match the masking used during computation.

    For calibration summaries the settings are read from the stored
    ``calibration.masking`` block so the report is internally consistent
    with the solver.  For plain *run* summaries the PlotSettings defaults
    are used (which is already consistent, since no computation-time
    masking is stored).
    """
    cal = getattr(series, "calibration", None)
    if cal is None:
        return PlotSettings()
    return PlotSettings(
        heating_eps_w=cal.masking_heating_eps_w,
        unmet_eps_kwh=cal.masking_unmet_eps_kwh,
        valid_min_fraction=cal.masking_valid_min_fraction,
        mask_block_hours=cal.masking_mask_block_hours,
    )
