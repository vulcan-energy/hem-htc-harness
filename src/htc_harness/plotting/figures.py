# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

import math

from ..empirical import _linear_fit_slope_intercept, _mean, _valid_fraction
from ..reader import read_results_static
from .models import PlotSettings, SummarySeries
from .utils import (
    AXIS_COLOR,
    COLORWAY,
    COMPONENT_COLORS,
    TEXT_COLOR,
    _annotate_mask_error,
    _apply_y_range,
    _base_layout,
    _bin_hours_label,
    _block_htc,
    _comp_label,
    _find_results_static,
    _get_offset_series,
    _keep_blocks,
    _make_agg_subplots,
    _mask_error_figure,
    _mask_inputs,
    _percentile,
    _plotly_imports,
    _ranges_for_hours,
    _weekly_masked_component_summary,
)

INPUTS_BASED_HTC_LABEL = "Inputs-based HTC"
OUTPUTS_BASED_UNFILTERED_LABEL = "Outputs-based HTC - unfiltered"
OUTPUTS_BASED_FILTERED_LABEL = "Outputs-based HTC - filtered"
OUTPUTS_BASED_REGRESSION_LABEL = "Outputs-based HTC - regression method"
OUTPUTS_BASED_REGRESSION_SELECTED_LABEL = (
    "Outputs-based HTC - regression method (selected)"
)
OUTPUTS_BASED_DIVISION_LABEL = "Outputs-based HTC - division method"


def _fig_htc_stability(series: SummarySeries, settings: PlotSettings):
    go, _, make_subplots = _plotly_imports()
    fig, agg_hours = _make_agg_subplots(series, settings, make_subplots)

    mask, mask_error = _mask_inputs(series, settings)
    system_unmasked = None
    system_masked = None
    system_series_by_offset = series.q_loss_by_offset_by_source.get(
        "system_gains_external", {}
    )
    if system_series_by_offset and all(
        str(k) in system_series_by_offset for k in series.offsets_k
    ):
        system_unmasked_series = _block_htc(
            series.offsets_k,
            system_series_by_offset,
            [
                (
                    0,
                    (
                        len(mask)
                        if mask is not None
                        else len(series.q_loss_by_offset.get("0.0", []))
                    ),
                )
            ],
            series.timestep_hours,
            None,
        )
        if system_unmasked_series and math.isfinite(system_unmasked_series[0]):
            system_unmasked = system_unmasked_series[0]
        if mask is not None:
            system_masked_series = _block_htc(
                series.offsets_k,
                system_series_by_offset,
                [(0, len(mask))],
                series.timestep_hours,
                mask,
            )
            if system_masked_series and math.isfinite(system_masked_series[0]):
                system_masked = system_masked_series[0]
    all_values: list[float] = []
    masked_means: dict[str, float] = {}
    unmasked_means: dict[str, float] = {}
    for col, hours in enumerate(agg_hours, start=1):
        ranges, labels, _ = _ranges_for_hours(series, hours)
        if not ranges:
            continue
        h_unmasked = _block_htc(
            series.offsets_k,
            series.q_loss_by_offset,
            ranges,
            series.timestep_hours,
            None,
        )
        if mask is None:
            h_masked = []
            kept = []
        else:
            h_masked = _block_htc(
                series.offsets_k,
                series.q_loss_by_offset,
                ranges,
                series.timestep_hours,
                mask,
            )
            kept = _keep_blocks(mask, ranges, settings)
        h_unmasked_clean = [h for h in h_unmasked if math.isfinite(h)]
        h_masked_clean = [h for h, k in zip(h_masked, kept) if k and math.isfinite(h)]
        all_values.extend(h_unmasked_clean)
        all_values.extend(h_masked_clean)
        if h_unmasked_clean:
            fig.add_trace(
                go.Violin(
                    y=h_unmasked_clean,
                    name="unmasked",
                    showlegend=(col == 1),
                    legendgroup="masking",
                    spanmode="hard",
                    line=dict(color=COLORWAY[1]),
                    fillcolor="rgba(42, 74, 79, 0.35)",
                    hovertemplate="%{y:.2f} W/K<extra></extra>",
                ),
                row=1,
                col=col,
            )
        if h_masked_clean:
            fig.add_trace(
                go.Violin(
                    y=h_masked_clean,
                    name="masked+filtered",
                    showlegend=(col == 1),
                    legendgroup="masking",
                    spanmode="hard",
                    line=dict(color=COLORWAY[3]),
                    fillcolor="rgba(183, 206, 196, 0.55)",
                    hovertemplate="%{y:.2f} W/K<extra></extra>",
                ),
                row=1,
                col=col,
            )
        masked_mean = _mean(h_masked_clean) if h_masked_clean else None
        unmasked_mean = _mean(h_unmasked_clean) if h_unmasked_clean else None
        if masked_mean is not None:
            label = _bin_hours_label(hours)
            masked_means[label] = masked_mean
            fig.add_trace(
                go.Scatter(
                    x=[-0.5, 0.5],
                    y=[masked_mean, masked_mean],
                    mode="lines",
                    name="masked mean",
                    line=dict(color=COLORWAY[0], width=2, dash="dot"),
                    showlegend=(col == 1),
                    legendgroup="htc-lines",
                    hovertemplate="masked %{y:.1f} W/K<extra></extra>",
                ),
                row=1,
                col=col,
            )
        if unmasked_mean is not None:
            label = _bin_hours_label(hours)
            unmasked_means[label] = unmasked_mean
        if mask_error and col == 1:
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

    for col in range(1, len(agg_hours) + 1):
        fig.update_yaxes(title_text="HTC (W/K)" if col == 1 else "", row=1, col=col)
        fig.update_xaxes(showticklabels=False, row=1, col=col)

    if (
        masked_means
        or unmasked_means
        or (series.htc_empirical is not None)
        or (series.htc_design is not None)
    ):
        lines = []
        if system_masked is not None:
            lines.append(f"System HTC (masked): {system_masked:.1f} W/K")
        if system_unmasked is not None:
            lines.append(f"System HTC (unmasked): {system_unmasked:.1f} W/K")
        if series.htc_design is not None and math.isfinite(series.htc_design):
            lines.append(f"Design HTC: {series.htc_design:.1f} W/K")
        for key in ("Daily", "Weekly", "Monthly"):
            if key in masked_means:
                lines.append(f"Masked mean ({key}): {masked_means[key]:.1f} W/K")
            if key in unmasked_means:
                lines.append(f"Unmasked mean ({key}): {unmasked_means[key]:.1f} W/K")
        meta = dict(fig.layout.meta) if isinstance(fig.layout.meta, dict) else {}
        meta["stats"] = "<br>".join(lines)
        meta["stats_class"] = "stats-box stats-columns"
        fig.update_layout(meta=meta)

    _apply_y_range(fig, all_values, agg_hours)

    _base_layout(fig, "HTC stability across aggregation levels")
    return fig


def _fig_offset_residuals(series: SummarySeries, settings: PlotSettings):
    go, _, make_subplots = _plotly_imports()
    fig, agg_hours = _make_agg_subplots(series, settings, make_subplots)

    mask, mask_error = _mask_inputs(series, settings)
    if mask is None:
        return _mask_error_figure(fig, mask_error, agg_hours, "Regression linearity")

    offsets = series.offsets_k
    all_residuals: list[float] = []
    for col, hours in enumerate(agg_hours, start=1):
        ranges, _, _ = _ranges_for_hours(series, hours)
        if not ranges:
            continue
        keep = _keep_blocks(mask, ranges, settings)
        residuals_by_offset: list[list[float]] = [[] for _ in offsets]
        for idx, (start, end) in enumerate(ranges):
            if not keep[idx]:
                continue
            window_mask = mask[start:end]
            q_vals: list[float] = []
            block_hours = 0.0
            for offset in offsets:
                series_q = _get_offset_series(series.q_loss_by_offset, offset)
                if series_q is None:
                    q_vals = []
                    break
                window = series_q[start:end]
                valid = [v for v, ok in zip(window, window_mask) if ok]
                block_hours = len(valid) * series.timestep_hours
                q_vals.append(sum(valid) * series.timestep_hours)
            if not q_vals or block_hours <= 0:
                continue
            try:
                m, a = _linear_fit_slope_intercept(offsets, q_vals)
            except ValueError:
                continue
            residuals_wh = [q - (a + m * x) for q, x in zip(q_vals, offsets)]
            residuals_w = [r / block_hours for r in residuals_wh]
            for i, r in enumerate(residuals_w):
                if math.isfinite(r):
                    residuals_by_offset[i].append(r)
                    all_residuals.append(r)
        if any(residuals_by_offset):
            pcts = {}
            for pct in (10, 25, 50, 75, 90):
                pcts[pct] = [
                    _percentile(vals, pct) if vals else 0.0
                    for vals in residuals_by_offset
                ]
            med, p10, p25, p75, p90 = pcts[50], pcts[10], pcts[25], pcts[75], pcts[90]
            fig.add_trace(
                go.Scatter(
                    x=list(offsets),
                    y=p75,
                    mode="lines",
                    line=dict(width=0),
                    showlegend=False,
                    legendgroup="residual-band",
                ),
                row=1,
                col=col,
            )
            fig.add_trace(
                go.Scatter(
                    x=list(offsets),
                    y=p25,
                    mode="lines",
                    line=dict(width=0),
                    fill="tonexty",
                    fillcolor="rgba(234, 253, 90, 0.25)",
                    showlegend=False,
                    legendgroup="residual-band",
                ),
                row=1,
                col=col,
            )
            if col == 1:
                fig.add_trace(
                    go.Scatter(
                        x=[None],
                        y=[None],
                        mode="lines",
                        line=dict(color="rgba(234, 253, 90, 0.35)", width=6),
                        name="IQR band",
                        showlegend=True,
                        legendgroup="residual-band",
                        hovertemplate="<extra></extra>",
                    ),
                    row=1,
                    col=col,
                )
            fig.add_trace(
                go.Scatter(
                    x=list(offsets),
                    y=med,
                    mode="lines",
                    line=dict(color=COLORWAY[0], width=2),
                    name="Median",
                    showlegend=(col == 1),
                    legendgroup="residual-median",
                    hovertemplate="ΔT %{x:.1f}K<br>median %{y:.2f} W<extra></extra>",
                ),
                row=1,
                col=col,
            )
            fig.add_trace(
                go.Scatter(
                    x=list(offsets),
                    y=p90,
                    mode="lines",
                    line=dict(color=COLORWAY[2], width=1, dash="dot"),
                    name="P10/P90 bounds",
                    showlegend=(col == 1),
                    legendgroup="residual-bounds",
                    hovertemplate="ΔT %{x:.1f}K<br>p90 %{y:.2f} W<extra></extra>",
                ),
                row=1,
                col=col,
            )
            fig.add_trace(
                go.Scatter(
                    x=list(offsets),
                    y=p10,
                    mode="lines",
                    line=dict(color=COLORWAY[2], width=1, dash="dot"),
                    showlegend=False,
                    legendgroup="residual-bounds",
                    hovertemplate="ΔT %{x:.1f}K<br>p10 %{y:.2f} W<extra></extra>",
                ),
                row=1,
                col=col,
            )
        fig.update_xaxes(title_text="ΔT (K)", row=1, col=col)
        fig.update_yaxes(title_text="residual (W)" if col == 1 else "", row=1, col=col)

    _base_layout(fig, "Regression linearity")
    # Keep residual scale visually stable so tiny residuals do not look oversized.
    if all_residuals:
        max_abs = max(abs(v) for v in all_residuals)
        half_span = max(1.0, max_abs * 1.05)
        for col in range(1, len(agg_hours) + 1):
            fig.update_yaxes(row=1, col=col, range=[-half_span, half_span])
    return fig


def _fig_htc_component_comparison(series: SummarySeries, settings: PlotSettings):
    go, _, make_subplots = _plotly_imports()
    fig, agg_hours = _make_agg_subplots(
        series,
        settings,
        make_subplots,
        rows=2,
        row_heights=[0.48, 0.52],
        vertical_spacing=0.12,
    )
    row1_vals: list[float] = []
    row2_vals: list[float] = []
    empirical_ref_color = "#7B1FA2"

    # Row 1: empirical breakdown (requires mask)
    #
    # We use:
    # - gated mask: drives which component values are included in the masked stack
    # - unmasked values: overlaid as faint lines for context
    mask_gated, mask_error = _mask_inputs(series, settings)
    if mask_gated is None:
        _annotate_mask_error(fig, mask_error, agg_hours)
    else:
        offsets = series.offsets_k
        components = list(COMPONENT_COLORS)
        for col, hours in enumerate(agg_hours, start=1):
            ranges, labels, _ = _ranges_for_hours(series, hours)
            if not ranges:
                continue
            keep = _keep_blocks(mask_gated, ranges, settings)
            value_mask = mask_gated
            xs = list(range(1, len(ranges) + 1))
            if labels:
                fig.update_xaxes(
                    tickmode="array",
                    tickvals=xs,
                    ticktext=labels,
                    row=1,
                    col=col,
                )
            comp_values_all: dict[str, list[float]] = {}
            comp_values_unmasked: dict[str, list[float]] = {}
            for comp in components:
                comp_series_by_offset: dict[str, list[float]] = {}
                for k, payload in series.q_loss_components_by_offset.items():
                    if comp in payload:
                        comp_series_by_offset[k] = payload[comp]
                if not comp_series_by_offset:
                    continue
                h_blocks = _block_htc(
                    offsets,
                    comp_series_by_offset,
                    ranges,
                    series.timestep_hours,
                    value_mask,
                )
                h_blocks_unmasked = _block_htc(
                    offsets, comp_series_by_offset, ranges, series.timestep_hours, None
                )
                h_raw = [h if math.isfinite(h) else float("nan") for h in h_blocks]
                h_raw_unmasked = [
                    h if math.isfinite(h) else float("nan") for h in h_blocks_unmasked
                ]
                comp_values_all[comp] = [v for v in h_raw]
                comp_values_unmasked[comp] = [v for v in h_raw_unmasked]
            totals = [0.0] * len(xs)
            for vals in comp_values_all.values():
                for i, v in enumerate(vals):
                    if math.isfinite(v):
                        totals[i] += v
            totals_unmasked = [0.0] * len(xs)
            for vals in comp_values_unmasked.values():
                for i, v in enumerate(vals):
                    if math.isfinite(v):
                        totals_unmasked[i] += v
            for idx, comp in enumerate(components):
                if comp not in comp_values_all:
                    continue
                h_all = comp_values_all[comp]
                h_kept = [
                    v if k and math.isfinite(v) else float("nan")
                    for v, k in zip(h_all, keep)
                ]
                row1_vals.extend([v for v in h_all if math.isfinite(v)])
                percents = [
                    (
                        v / totals[i] * 100.0
                        if math.isfinite(v) and totals[i] != 0
                        else float("nan")
                    )
                    for i, v in enumerate(h_kept)
                ]
                label = _comp_label(comp)
                fig.add_trace(
                    go.Scatter(
                        x=xs,
                        y=h_kept,
                        name=f"Regression {label}",
                        showlegend=(col == 1),
                        legend="legend",
                        legendgroup="empirical-components",
                        mode="lines",
                        stackgroup=f"empirical-{col}",
                        line=dict(
                            color=COMPONENT_COLORS.get(
                                comp, COLORWAY[idx % len(COLORWAY)]
                            ),
                            width=0.8,
                        ),
                        fill="tonexty",
                        customdata=percents,
                        hovertemplate=(
                            f"{label} "
                            + "%{y:.2f} W/K<br>Share %{customdata:.0f}%<extra></extra>"
                        ),
                    ),
                    row=1,
                    col=col,
                )
                h_kept_unmasked = comp_values_unmasked[comp]
                percents_unmasked = [
                    (
                        v / totals_unmasked[i] * 100.0
                        if math.isfinite(v) and totals_unmasked[i] != 0
                        else float("nan")
                    )
                    for i, v in enumerate(h_kept_unmasked)
                ]
                fig.add_trace(
                    go.Scatter(
                        x=xs,
                        y=h_kept_unmasked,
                        name=f"Regression {label} (unfiltered)",
                        showlegend=(col == 1),
                        legend="legend",
                        legendgroup="empirical-components-unmasked",
                        mode="lines",
                        line=dict(
                            color=COMPONENT_COLORS.get(
                                comp, COLORWAY[idx % len(COLORWAY)]
                            ),
                            width=0.6,
                            dash="dot",
                        ),
                        opacity=0.16,
                        customdata=percents_unmasked,
                        hovertemplate=(
                            f"{label} (unfiltered) "
                            + "%{y:.2f} W/K<br>Share %{customdata:.0f}%<extra></extra>"
                        ),
                    ),
                    row=1,
                    col=col,
                )
            h_empirical_top = _block_htc(
                series.offsets_k,
                series.q_loss_by_offset,
                ranges,
                series.timestep_hours,
                None,
            )
            row1_vals.extend([v for v in h_empirical_top if math.isfinite(v)])
            fig.add_trace(
                go.Scatter(
                    x=xs,
                    y=h_empirical_top,
                    mode="lines",
                    name="Regression total",
                    line=dict(color=empirical_ref_color, width=2.2, dash="dot"),
                    showlegend=(col == 1),
                    legend="legend",
                    legendgroup="empirical-total",
                    hovertemplate="Regression total %{y:.2f} W/K<extra></extra>",
                ),
                row=1,
                col=col,
            )
            fig.update_yaxes(
                title_text=f"{OUTPUTS_BASED_REGRESSION_SELECTED_LABEL} (W/K)"
                if col == 1
                else "",
                row=1,
                col=col,
            )
            fig.update_xaxes(title_text="timeblock", row=1, col=col)

    # Row 2: division-method breakdown (sum(Q)/sum(ΔT))
    internal = series.internal_air_temp_c
    external = series.external_air_temp_c
    if not internal or not external:
        for col in range(1, len(agg_hours) + 1):
            fig.add_annotation(
                text="Missing internal/external air temperatures",
                x=0.5,
                y=0.5,
                xref="x domain",
                yref="y domain",
                showarrow=False,
                font=dict(size=11, color=TEXT_COLOR),
                row=2,
                col=col,
            )
    else:
        n = min(len(internal), len(external))
        if n > 0:
            dt_series = [internal[i] - external[i] for i in range(n)]
            base_loss = _get_offset_series(series.q_loss_by_offset, 0.0) or []
            n_series = min(n, len(base_loss) if base_loss else n)
            dt_series = dt_series[:n_series]
            base_key = "0.0"
            components = series.hem_htc_components_by_offset.get(base_key, {})
            sorted_components = sorted(components)
            component_series = {
                comp: [v if v is not None else float("nan") for v in values[:n_series]]
                for comp, values in components.items()
            }
            for col, hours in enumerate(agg_hours, start=1):
                ranges, labels, _ = _ranges_for_hours(series, hours)
                if not ranges:
                    continue
                clipped = []
                for start, end in ranges:
                    if start >= n_series:
                        continue
                    clipped.append((start, min(end, n_series)))
                if not clipped:
                    continue
                xs = list(range(1, len(clipped) + 1))
                if labels:
                    fig.update_xaxes(
                        tickmode="array",
                        tickvals=xs,
                        ticktext=labels,
                        row=2,
                        col=col,
                    )
                comp_values: dict[str, list[float]] = {
                    comp: [] for comp in sorted_components
                }
                totals: list[float] = []
                for start, end in clipped:
                    window_dt = [v for v in dt_series[start:end] if math.isfinite(v)]
                    total_dt = sum(window_dt)
                    if total_dt == 0.0:
                        for comp in sorted_components:
                            comp_values[comp].append(float("nan"))
                        totals.append(float("nan"))
                        continue
                    total_q = 0.0
                    for comp in sorted_components:
                        comp_series = component_series.get(comp, [])
                        comp_q = 0.0
                        for htc, dt in zip(
                            comp_series[start:end], dt_series[start:end], strict=False
                        ):
                            if math.isfinite(htc) and math.isfinite(dt):
                                comp_q += htc * dt
                        comp_values[comp].append(comp_q / total_dt)
                        total_q += comp_q
                    totals.append(total_q / total_dt)

                for idx, comp in enumerate(sorted_components):
                    color = COMPONENT_COLORS.get(comp, COLORWAY[idx % len(COLORWAY)])
                    series_vals = comp_values[comp]
                    row2_vals.extend([v for v in series_vals if math.isfinite(v)])
                    fig.add_trace(
                        go.Scatter(
                            x=xs,
                            y=series_vals,
                            mode="lines",
                            name=f"Division {comp}",
                            stackgroup=f"dynamic-{col}",
                            hovertemplate=f"{comp} " + "%{y:.2f} W/K<extra></extra>",
                            line=dict(color=color, width=1.2),
                            fillcolor=color,
                            showlegend=(col == 1),
                            legend="legend2",
                            legendgroup="dynamic-components",
                        ),
                        row=2,
                        col=col,
                    )
                row2_vals.extend([v for v in totals if math.isfinite(v)])
                fig.add_trace(
                    go.Scatter(
                        x=xs,
                        y=totals,
                        mode="lines",
                        name="Division total",
                        line=dict(color=TEXT_COLOR, width=2.6),
                        showlegend=(col == 1),
                        legend="legend2",
                        legendgroup="dynamic-components",
                        hovertemplate="Division total %{y:.2f} W/K<extra></extra>",
                    ),
                    row=2,
                    col=col,
                )

                h_empirical = _block_htc(
                    series.offsets_k,
                    series.q_loss_by_offset,
                    clipped,
                    series.timestep_hours,
                    None,
                )
                row2_vals.extend([v for v in h_empirical if math.isfinite(v)])
                fig.add_trace(
                    go.Scatter(
                        x=xs,
                        y=h_empirical,
                        mode="lines",
                        name="Regression total",
                        line=dict(color=empirical_ref_color, width=2.2, dash="dot"),
                        showlegend=(col == 1),
                        legend="legend2",
                        legendgroup="dynamic-components",
                        hovertemplate="Regression %{y:.2f} W/K<extra></extra>",
                    ),
                    row=2,
                    col=col,
                )
                fig.update_yaxes(
                    title_text=f"{OUTPUTS_BASED_DIVISION_LABEL} (W/K)"
                    if col == 1
                    else "",
                    row=2,
                    col=col,
                )
                fig.update_xaxes(title_text="timeblock", row=2, col=col)

    # Apply percentile-based y-limits to reduce spike dominance.
    _apply_y_range(fig, row1_vals, agg_hours, row=1, pct=(2, 98))
    _apply_y_range(fig, row2_vals, agg_hours, row=2, pct=(2, 98))

    _base_layout(fig, "Outputs-based HTC methods by component")
    fig.update_layout(
        legend=dict(
            x=1.02,
            y=0.98,
            xanchor="left",
            yanchor="top",
        ),
        legend2=dict(
            x=1.02,
            y=0.36,
            xanchor="left",
            yanchor="top",
        ),
    )
    return fig


def _fig_calibration_waterfall(
    series: SummarySeries,
    settings: PlotSettings,
):
    """Waterfall showing how calibration bridges baseline to target HTC."""
    go, _, _ = _plotly_imports()
    cal = series.calibration
    if cal is None:
        return None
    if cal.htc_calibrated_w_per_k is None:
        return None

    baseline = cal.htc_baseline_w_per_k
    calibrated = cal.htc_calibrated_w_per_k
    target = cal.htc_target_w_per_k
    cal_effect = calibrated - baseline
    residual = target - calibrated

    iter_steps = [
        step
        for step in cal.iterative_history
        if isinstance(step.get("htc_after_w_per_k"), (int, float))
    ]
    if iter_steps:
        categories = ["Baseline"]
        measures = ["absolute"]
        values = [baseline]
        text = [f"{baseline:.1f}"]
        prev = baseline
        for step in iter_steps:
            step_no = step.get("step")
            step_label = (
                f"Iter {int(step_no)}"
                if isinstance(step_no, int)
                else f"Iter {len(categories)}"
            )
            step_htc = float(step["htc_after_w_per_k"])
            categories.append(step_label)
            measures.append("relative")
            values.append(step_htc - prev)
            text.append(f"{(step_htc - prev):+.1f}")
            prev = step_htc
        categories.extend(["Calibrated", "Residual", "Target"])
        measures.extend(["total", "relative", "total"])
        values.extend([0, residual, 0])
        text.extend([f"{calibrated:.1f}", f"{residual:+.1f}", f"{target:.1f}"])
    else:
        categories = [
            "Baseline",
            "Calibration\neffect",
            "Calibrated",
            "Residual",
            "Target",
        ]
        measures = ["absolute", "relative", "total", "relative", "total"]
        values = [baseline, cal_effect, 0, residual, 0]
        text = [
            f"{baseline:.1f}",
            f"{cal_effect:+.1f}",
            f"{calibrated:.1f}",
            f"{residual:+.1f}",
            f"{target:.1f}",
        ]

    color_increase = COMPONENT_COLORS["FabricHeatLoss"]
    color_decrease = COMPONENT_COLORS["InfiltrationVentilation"]
    color_total = "#1A3437"

    fig = go.Figure(
        go.Waterfall(
            orientation="v",
            x=categories,
            y=values,
            measure=measures,
            text=text,
            textposition="outside",
            textfont=dict(size=12),
            connector=dict(line=dict(color="#B0B0B0", width=1, dash="dot")),
            increasing=dict(marker=dict(color=color_increase)),
            decreasing=dict(marker=dict(color=color_decrease)),
            totals=dict(marker=dict(color=color_total)),
        )
    )

    fig.add_hline(
        y=target,
        line=dict(color="#E15759", width=2, dash="dash"),
        annotation=dict(
            text=f"Target: {target:.1f} W/K",
            font=dict(size=11, color="#E15759"),
            xanchor="left",
        ),
    )

    _base_layout(fig, "Calibration waterfall")
    fig.update_layout(
        yaxis_title="HTC (W/K)",
        yaxis=dict(range=[0, 350], fixedrange=True),
        height=420,
        showlegend=False,
        margin=dict(l=55, r=40, t=80, b=70),
    )

    return fig


def _scale_headline_components(
    values: dict[str, float | None],
    comp_labels: list[str],
    total_override: float | None = None,
) -> dict[str, float | None]:
    scaled = dict(values)
    comp_vals = [scaled.get(lbl) for lbl in comp_labels]
    if (
        isinstance(total_override, (int, float))
        and math.isfinite(total_override)
        and all(isinstance(v, (int, float)) and math.isfinite(v) for v in comp_vals)
    ):
        comp_sum = sum(float(v) for v in comp_vals)
        if comp_sum > 0:
            scale = float(total_override) / comp_sum
            for lbl in comp_labels:
                vv = scaled.get(lbl)
                scaled[lbl] = (
                    float(vv) * scale
                    if isinstance(vv, (int, float)) and math.isfinite(vv)
                    else None
                )
        scaled["Total"] = float(total_override)
        return scaled
    parts = [v for v in (scaled.get(cl) for cl in comp_labels) if v is not None]
    scaled["Total"] = sum(parts) if parts else None
    return scaled


def _headline_static_components(
    series: SummarySeries,
    comp_labels: list[str],
    *,
    total_override: float | None = None,
) -> dict[str, float | None]:
    static: dict[str, float | None] = {lbl: None for lbl in comp_labels}
    static["Total"] = None
    if series.base_results_dir is None:
        return static
    results_static_path = _find_results_static(series.base_results_dir)
    if results_static_path is None:
        return static
    sv = read_results_static(results_static_path)
    static["Fabric"] = sv.get("Fabric heat transfer coefficient")
    static["Thermal Bridges"] = sv.get("Thermal bridges heat transfer coefficient")
    static["Ventilation"] = sv.get("Ventilation heat transfer coefficient")
    static["Total"] = sv.get("Heat transfer coefficient")
    return _scale_headline_components(static, comp_labels, total_override)


def _headline_empirical_components(
    series: SummarySeries,
    comp_keys: list[str],
    comp_labels: list[str],
    *,
    total_override: float | None = None,
) -> dict[str, float | None]:
    n_ts = len(series.q_loss_by_offset.get("0.0", []))
    full_range = [(0, n_ts)] if n_ts else []
    empirical: dict[str, float | None] = {}
    for key, lbl in zip(comp_keys, comp_labels):
        comp_by_offset: dict[str, list[float]] = {}
        for k, payload in series.q_loss_components_by_offset.items():
            if key in payload:
                comp_by_offset[k] = payload[key]
        if comp_by_offset and full_range:
            h = _block_htc(
                series.offsets_k,
                comp_by_offset,
                full_range,
                series.timestep_hours,
                None,
            )
            empirical[lbl] = h[0] if h and math.isfinite(h[0]) else None
        else:
            empirical[lbl] = None
    return _scale_headline_components(empirical, comp_labels, total_override)


def _headline_filtered_components(
    series: SummarySeries,
    settings: PlotSettings,
    *,
    total_override: float | None = None,
) -> dict[str, float | None]:
    weekly = _weekly_masked_component_summary(series, settings)
    filtered: dict[str, float | None] = {
        "Fabric": weekly.get("weekly_fabric_w_per_k"),
        "Thermal Bridges": weekly.get("weekly_bridges_w_per_k"),
        "Ventilation": weekly.get("weekly_vent_w_per_k"),
        "Total": weekly.get("weekly_masked_mean_w_per_k"),
    }
    return _scale_headline_components(
        filtered, ["Fabric", "Thermal Bridges", "Ventilation"], total_override
    )


def _fig_headline_stacked_bar(
    series: SummarySeries,
    settings: PlotSettings,
    baseline_series: SummarySeries | None = None,
):
    """Horizontal stacked bar: 3 headline HTC metrics decomposed by Fabric / TB / Vent."""
    go, _, _ = _plotly_imports()

    comp_keys = ["FabricHeatLoss", "ThermalBridges", "InfiltrationVentilation"]
    comp_labels = ["Fabric", "Thermal Bridges", "Ventilation"]
    comp_colors = [COMPONENT_COLORS[k] for k in comp_keys]

    cal = series.calibration

    if cal is not None and baseline_series is not None:
        static_baseline = _headline_static_components(
            baseline_series,
            comp_labels,
            total_override=cal.htc_static_baseline_w_per_k,
        )
        static_calibrated = _headline_static_components(
            series,
            comp_labels,
            total_override=cal.htc_static_calibrated_w_per_k,
        )
        empirical_baseline = _headline_empirical_components(
            baseline_series,
            comp_keys,
            comp_labels,
            total_override=cal.htc_empirical_baseline_w_per_k,
        )
        empirical_calibrated = _headline_empirical_components(
            series,
            comp_keys,
            comp_labels,
            total_override=cal.htc_empirical_calibrated_w_per_k,
        )
        filtered_baseline = _headline_filtered_components(
            baseline_series,
            settings,
            total_override=cal.htc_baseline_w_per_k,
        )
        filtered_calibrated = _headline_filtered_components(
            series,
            settings,
            total_override=cal.htc_calibrated_w_per_k,
        )
        metrics_list: list[tuple[str, dict[str, float | None]]] = [
            (f"{INPUTS_BASED_HTC_LABEL}<br>Baseline", static_baseline),
            (f"{INPUTS_BASED_HTC_LABEL}<br>Calibrated", static_calibrated),
            (f"{OUTPUTS_BASED_UNFILTERED_LABEL}<br>Baseline", empirical_baseline),
            (f"{OUTPUTS_BASED_UNFILTERED_LABEL}<br>Calibrated", empirical_calibrated),
            (f"{OUTPUTS_BASED_FILTERED_LABEL}<br>Baseline", filtered_baseline),
            (f"{OUTPUTS_BASED_FILTERED_LABEL}<br>Calibrated", filtered_calibrated),
        ]
    else:
        static = _headline_static_components(series, comp_labels)
        empirical = _headline_empirical_components(series, comp_keys, comp_labels)
        calibration = _headline_filtered_components(
            series,
            settings,
            total_override=(
                float(cal.htc_calibrated_w_per_k)
                if cal is not None and cal.htc_calibrated_w_per_k is not None
                else None
            ),
        )
        metrics_list = [
            (INPUTS_BASED_HTC_LABEL, static),
            (OUTPUTS_BASED_UNFILTERED_LABEL, empirical),
            (OUTPUTS_BASED_FILTERED_LABEL, calibration),
        ]

    fig = go.Figure()

    display_order = [m[0] for m in metrics_list]
    display_order.reverse()

    for lbl, color in zip(comp_labels, comp_colors):
        values = []
        text_vals = []
        for name, data in metrics_list:
            v = data.get(lbl)
            values.append(v)
            text_vals.append(
                f"{v:.1f}" if isinstance(v, (int, float)) and math.isfinite(v) else ""
            )
        fig.add_trace(
            go.Bar(
                y=[m[0] for m in metrics_list],
                x=values,
                name=lbl,
                orientation="h",
                marker_color=color,
                text=text_vals,
                textposition="inside",
                textfont=dict(color="white", size=11),
                hovertemplate=f"{lbl}: " + "%{x:.1f} W/K<extra></extra>",
            )
        )

    for name, data in metrics_list:
        total = data.get("Total")
        if isinstance(total, (int, float)) and math.isfinite(total):
            fig.add_annotation(
                x=total,
                y=name,
                text=f"  {total:.1f} W/K",
                showarrow=False,
                xanchor="left",
                font=dict(size=12, color=TEXT_COLOR),
            )

    if cal is not None:
        target = cal.htc_target_w_per_k
        fig.add_vline(
            x=target,
            line_width=2,
            line_dash="dash",
            line_color="#FF6B6B",
            annotation_text=f"Target: {target:.1f} W/K",
            annotation_position="top right",
            annotation_font_color="#FF6B6B",
        )

    n_bars = len(metrics_list)
    chart_height = max(320, 70 * n_bars + 60)
    fig.update_layout(
        barmode="stack",
        xaxis_title="HTC (W/K)",
        yaxis=dict(categoryorder="array", categoryarray=display_order),
        height=chart_height,
        margin=dict(l=220),
    )
    # Keep the comparison scale fixed across reports for easier visual scanning.
    fig.update_xaxes(range=[0, 350], fixedrange=True)
    _base_layout(fig, "Headline HTC comparison")
    return fig


def _fig_htc_masking_time_series(series: SummarySeries, settings: PlotSettings):
    """Weekly HTC profile with kept/dropped blocks as background shading."""
    go, _, _ = _plotly_imports()
    hours = 168.0
    ranges, labels, _ = _ranges_for_hours(series, hours)
    if not ranges:
        return None

    mask, mask_error = _mask_inputs(series, settings)
    fig = go.Figure()

    if mask is None:
        fig.add_annotation(
            text=mask_error or "Mask unavailable",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font=dict(size=12, color=TEXT_COLOR),
        )
        _base_layout(fig, "HTC & masking time series")
        return fig

    keep = _keep_blocks(mask, ranges, settings)
    fractions = _valid_fraction(mask, ranges)

    h_unmasked = _block_htc(
        series.offsets_k,
        series.q_loss_by_offset,
        ranges,
        series.timestep_hours,
        None,
    )
    h_masked = _block_htc(
        series.offsets_k,
        series.q_loss_by_offset,
        ranges,
        series.timestep_hours,
        mask,
    )

    xs = list(range(1, len(ranges) + 1))
    masked_vals = [
        h if k and math.isfinite(h) else float("nan") for h, k in zip(h_masked, keep)
    ]
    unmasked_vals = [h if math.isfinite(h) else float("nan") for h in h_unmasked]

    for i, (x, k) in enumerate(zip(xs, keep)):
        color = "rgba(183, 206, 196, 0.25)" if k else "rgba(255, 107, 107, 0.10)"
        fig.add_vrect(
            x0=x - 0.5,
            x1=x + 0.5,
            fillcolor=color,
            layer="below",
            line_width=0,
        )

    fig.add_trace(
        go.Scatter(
            x=xs,
            y=unmasked_vals,
            mode="lines",
            name="Unmasked HTC",
            line=dict(color=COLORWAY[3], width=1.2, dash="dot"),
            hovertemplate="Week %{x}: %{y:.1f} W/K (unmasked)<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=xs,
            y=masked_vals,
            mode="lines",
            name="Masked HTC",
            line=dict(color=COLORWAY[0], width=2),
            hovertemplate="Week %{x}: %{y:.1f} W/K (masked)<extra></extra>",
        )
    )

    fig.add_trace(
        go.Scatter(
            x=xs,
            y=fractions,
            mode="lines",
            name="Valid fraction",
            line=dict(color="rgba(78, 121, 167, 0.5)", width=1),
            fill="tozeroy",
            fillcolor="rgba(78, 121, 167, 0.08)",
            yaxis="y2",
            hovertemplate="Week %{x}: %{y:.2f} valid fraction<extra></extra>",
        )
    )

    if settings.valid_min_fraction is not None:
        fig.add_hline(
            y=settings.valid_min_fraction,
            line_width=1,
            line_dash="dash",
            line_color=AXIS_COLOR,
        )

    if labels:
        fig.update_xaxes(tickmode="array", tickvals=xs, ticktext=labels)
    fig.update_layout(
        xaxis_title="Week",
        yaxis_title="HTC (W/K)",
        yaxis2=dict(
            title="Valid fraction",
            overlaying="y",
            side="right",
            range=[0, 1.05],
            showgrid=False,
        ),
        height=420,
    )
    _base_layout(fig, "HTC & masking time series")
    return fig


def _fig_masking_inputs(series: SummarySeries, settings: PlotSettings):
    """Weekly space-heating and unmet-demand used to build the per-timestep mask."""
    go, _, make_subplots = _plotly_imports()
    hours = 168.0
    ranges, labels, _ = _ranges_for_hours(series, hours)
    if not ranges:
        return None

    heating = _get_offset_series(series.q_system_total_by_offset, 0.0)
    if heating is None:
        for key in ("Living radiator: energy output", "space heat demand"):
            payload = series.base_results_series.get(key)
            if isinstance(payload, dict) and isinstance(payload.get("values"), list):
                vals = [float(v) for v in payload["values"]]
                if "kWh" in str(payload.get("unit") or ""):
                    vals = [v * 1000.0 / series.timestep_hours for v in vals]
                heating = vals
                break
    unmet_raw = series.base_results_series.get(
        "_unmet_demand: Living"
    ) or series.base_results_series.get("_unmet_demand: total")
    unmet_values: list[float] | None = None
    if unmet_raw and isinstance(unmet_raw.get("values"), list):
        unmet_values = [float(v) for v in unmet_raw["values"]]

    if heating is None and unmet_values is None:
        return None

    mask, _ = _mask_inputs(series, settings)
    keep = _keep_blocks(mask, ranges, settings) if mask else [True] * len(ranges)

    def _weekly_mean(ts: list[float] | None) -> list[float]:
        if ts is None:
            return [float("nan")] * len(ranges)
        out = []
        for start, end in ranges:
            sl = ts[start:end]
            valid = [v for v in sl if math.isfinite(v)]
            out.append(sum(valid) / len(valid) if valid else float("nan"))
        return out

    def _weekly_max(ts: list[float] | None) -> list[float]:
        if ts is None:
            return [float("nan")] * len(ranges)
        out = []
        for start, end in ranges:
            sl = ts[start:end]
            valid = [v for v in sl if math.isfinite(v)]
            out.append(max(valid) if valid else float("nan"))
        return out

    heat_weekly = _weekly_mean(heating)
    unmet_weekly = _weekly_max(unmet_values)
    xs = list(range(1, len(ranges) + 1))

    total_heating_kwh = (
        sum(v for v in heating if math.isfinite(v)) * series.timestep_hours / 1000.0
        if heating is not None
        else None
    )
    total_unmet_kwh = (
        sum(v for v in unmet_values if math.isfinite(v))
        if unmet_values is not None
        else None
    )

    fig = make_subplots(
        rows=2,
        cols=1,
        subplot_titles=("Mean space heating per week", "Max unmet demand per week"),
        row_heights=[0.5, 0.5],
        vertical_spacing=0.14,
        shared_xaxes=True,
    )

    for x, k in zip(xs, keep):
        color = "rgba(183, 206, 196, 0.25)" if k else "rgba(255, 107, 107, 0.10)"
        fig.add_vrect(
            x0=x - 0.5, x1=x + 0.5, fillcolor=color, layer="below", line_width=0
        )

    fig.add_trace(
        go.Bar(
            x=xs,
            y=heat_weekly,
            name="Space heating",
            marker_color="#1A3437",
            hovertemplate="Week %{x}: %{y:.0f} W<extra></extra>",
        ),
        row=1,
        col=1,
    )

    fig.add_hline(
        y=settings.heating_eps_w,
        line_width=1.5,
        line_dash="dash",
        line_color="#E15759",
        annotation_text=f"Threshold: {settings.heating_eps_w:.0f} W",
        annotation_position="top right",
        annotation_font=dict(size=10, color="#E15759"),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Bar(
            x=xs,
            y=unmet_weekly,
            name="Unmet demand",
            marker_color="#F28E2B",
            hovertemplate="Week %{x}: %{y:.3f} kWh<extra></extra>",
        ),
        row=2,
        col=1,
    )

    fig.add_hline(
        y=settings.unmet_eps_kwh,
        line_width=1.5,
        line_dash="dash",
        line_color="#E15759",
        annotation_text=f"Threshold: {settings.unmet_eps_kwh:.2f} kWh",
        annotation_position="top right",
        annotation_font=dict(size=10, color="#E15759"),
        row=2,
        col=1,
    )

    if labels:
        fig.update_xaxes(tickmode="array", tickvals=xs, ticktext=labels, row=2, col=1)

    fig.update_yaxes(title_text="Heating (W)", row=1, col=1)
    fig.update_yaxes(title_text="Unmet demand (kWh)", row=2, col=1)
    fig.update_xaxes(title_text="Week", row=2, col=1)

    _base_layout(fig, "Masking inputs")
    fig.update_layout(height=600, showlegend=False, margin=dict(l=50, r=40))
    meta = dict(fig.layout.meta) if isinstance(fig.layout.meta, dict) else {}
    meta["mask_total_heating_kwh"] = (
        float(total_heating_kwh) if total_heating_kwh is not None else None
    )
    meta["mask_total_unmet_kwh"] = (
        float(total_unmet_kwh) if total_unmet_kwh is not None else None
    )
    fig.update_layout(meta=meta)
    return fig


# Section keys for report structure.
SECTION_RESULTS = "results"
SECTION_BREAKDOWNS = "breakdowns"
SECTION_DATA_QUALITY = "data_quality"


def _tag_section(fig, section: str):
    """Tag a figure with its report section in layout.meta."""
    if fig is None:
        return None
    meta = dict(fig.layout.meta) if isinstance(fig.layout.meta, dict) else {}
    meta["section"] = section
    fig.update_layout(meta=meta)
    return fig
