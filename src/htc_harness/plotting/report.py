# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

import math
import webbrowser
from html import escape
from pathlib import Path

from .figures import (
    SECTION_BREAKDOWNS,
    SECTION_DATA_QUALITY,
    SECTION_RESULTS,
    _fig_calibration_waterfall,
    _fig_headline_stacked_bar,
    _fig_htc_component_comparison,
    _fig_htc_masking_time_series,
    _fig_htc_stability,
    _fig_masking_inputs,
    _fig_offset_residuals,
    _tag_section,
)
from .loader import load_summary_series
from .models import PlotSettings, SummarySeries
from .utils import (
    PAPER_BG,
    PLOT_FONT,
    TEXT_COLOR,
    _mask_inputs,
    _plotly_imports,
    _weekly_masked_component_summary,
)

INPUTS_BASED_HTC_LABEL = "Inputs-based HTC"
OUTPUTS_BASED_UNFILTERED_LABEL = "Outputs-based HTC - unfiltered"
OUTPUTS_BASED_FILTERED_LABEL = "Outputs-based HTC - filtered"
OUTPUTS_BASED_REGRESSION_LABEL = "Outputs-based HTC - regression method"
OUTPUTS_BASED_DIVISION_LABEL = "Outputs-based HTC - division method"


def _html_text(value: object) -> str:
    return escape(str(value), quote=False)


def _html_attr(value: object) -> str:
    return escape(str(value), quote=True)


def build_plotly_figures(
    series: SummarySeries,
    settings: PlotSettings,
    baseline_series: SummarySeries | None = None,
):
    weekly_settings = PlotSettings(
        heating_eps_w=settings.heating_eps_w,
        unmet_eps_kwh=settings.unmet_eps_kwh,
        valid_min_fraction=settings.valid_min_fraction,
        mask_block_hours=settings.mask_block_hours,
        agg_hours=(168.0,),
    )
    figures = []

    # Results
    stacked = _fig_headline_stacked_bar(
        series, settings, baseline_series=baseline_series
    )
    figures.append(_tag_section(stacked, SECTION_RESULTS))

    # Breakdowns
    comp = _fig_htc_component_comparison(series, weekly_settings)
    figures.append(_tag_section(comp, SECTION_BREAKDOWNS))
    waterfall = _fig_calibration_waterfall(series, settings)
    figures.append(_tag_section(waterfall, SECTION_BREAKDOWNS))

    # Data Quality
    masking_ts = _fig_htc_masking_time_series(series, settings)
    figures.append(_tag_section(masking_ts, SECTION_DATA_QUALITY))
    mask_inputs = _fig_masking_inputs(series, settings)
    figures.append(_tag_section(mask_inputs, SECTION_DATA_QUALITY))
    residuals = _fig_offset_residuals(series, weekly_settings)
    figures.append(_tag_section(residuals, SECTION_DATA_QUALITY))
    stability = _fig_htc_stability(series, settings)
    figures.append(_tag_section(stability, SECTION_DATA_QUALITY))

    return [f for f in figures if f is not None]


def _load_calibration_baseline_series(
    series: SummarySeries,
) -> SummarySeries | None:
    if series.calibration is None:
        return None
    if series.path.stem.endswith("_baseline"):
        return None
    baseline_path = series.path.with_name(
        f"{series.path.stem}_baseline{series.path.suffix or '.json'}"
    )
    if not baseline_path.exists():
        return None
    try:
        return load_summary_series(baseline_path)
    except Exception:
        return None


def _render_figure(fig, pio, include_js: str | bool) -> str:
    """Render a single figure div with header, purpose, stats, and chart."""
    meta = fig.layout.meta if isinstance(fig.layout.meta, dict) else {}
    fig_id = str(meta.get("fig_id", ""))
    title_text = str(meta.get("title") or "")
    stats = str(meta.get("stats") or "")
    stats_class = str(meta.get("stats_class") or "stats-box")

    title_html = (
        f'<div class="figure-title">{_html_text(title_text)}</div>'
        if title_text
        else ""
    )
    safe_stats = "<br>".join(_html_text(part) for part in stats.split("<br>"))
    stats_html = (
        f'<div class="{_html_attr(stats_class)}">{safe_stats}</div>' if stats else ""
    )
    meta_html = ""
    if stats_html:
        meta_html = f'<div class="figure-meta">{stats_html}</div>'
    header_html = ""
    if title_html or meta_html:
        header_html = f'<div class="figure-header">{title_html}{meta_html}</div>'
    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=include_js)
    id_attr = f' id="{_html_attr(fig_id)}"' if fig_id else ""
    mask_total_heating = meta.get("mask_total_heating_kwh")
    mask_total_unmet = meta.get("mask_total_unmet_kwh")
    side_metrics_html = ""
    if isinstance(mask_total_heating, (int, float)) or isinstance(
        mask_total_unmet, (int, float)
    ):
        figure_height = getattr(fig.layout, "height", None)
        side_height_px = (
            int(figure_height)
            if isinstance(figure_height, (int, float)) and figure_height > 0
            else 600
        )
        cards: list[str] = []
        if isinstance(mask_total_heating, (int, float)):
            cards.append(
                '<div class="side-metric-card">'
                '<div class="side-metric-label">Total heating</div>'
                f'<div class="side-metric-value">{float(mask_total_heating):.1f}</div>'
                '<div class="side-metric-unit">kWh</div>'
                "</div>"
            )
        if isinstance(mask_total_unmet, (int, float)):
            cards.append(
                '<div class="side-metric-card">'
                '<div class="side-metric-label">Total unmet demand</div>'
                f'<div class="side-metric-value">{float(mask_total_unmet):.2f}</div>'
                '<div class="side-metric-unit">kWh</div>'
                "</div>"
            )
        side_metrics_html = (
            f'<div class="figure-with-side-metrics" style="--side-metrics-height: {side_height_px}px">'
            f'<div class="side-metrics-column">{"".join(cards)}</div>'
            f'<div class="side-metrics-chart">{chart_html}</div>'
            "</div>"
        )
    body_html = side_metrics_html or chart_html
    return f'<div class="report-figure"{id_attr}>{header_html}{body_html}</div>'


def _build_assumptions_html(series: SummarySeries, settings: PlotSettings) -> str:
    """Build grouped assumptions HTML with brief explanations."""

    def _dl(items: list[tuple[str, str]]) -> str:
        rows = "".join(
            f"<tr><td><strong>{_html_text(k)}</strong></td>"
            f"<td>{_html_text(v)}</td></tr>"
            for k, v in items
        )
        return f'<table class="assumptions-table"><tbody>{rows}</tbody></table>'

    parts: list[str] = []

    # --- 1. HEM defaults (always shown first) ---
    has_fhs = "--future-homes-standard" in series.extra_flags
    fhs_schedule_off = (
        series.env_overrides.get("HEM_FHS_DISABLE_HEATING_SCHEDULE") == "1"
    )

    engine_str = ""
    if series.engine_runner:
        engine_str = f"{series.engine_runner.capitalize()} HEM"
        if series.engine_commit:
            engine_str += f", commit {series.engine_commit}"

    defaults: list[tuple[str, str]] = []
    if engine_str:
        defaults.append(("Engine", engine_str))
    if has_fhs:
        defaults.append(
            (
                "FHS assumptions",
                "Enabled \u2014 HEM uses Future Homes Standard defaults "
                "(standardised occupancy, internal gains, climate)."
                " The FHS heating schedule is "
                + (
                    "disabled (heated 24/7 at setpoint) to isolate fabric performance"
                    if fhs_schedule_off
                    else "active"
                )
                + ".",
            )
        )
    elif fhs_schedule_off:
        defaults.append(("Heating schedule", "Disabled (24/7 at setpoint)"))
    defaults.append(("Timestep", f"{series.timestep_hours:g} h"))

    window_str = "Full year"
    if series.analysis_window_type == "index":
        if series.analysis_window_start == 0 and series.analysis_window_end is not None:
            total_hours = series.analysis_window_end * series.timestep_hours
            if abs(total_hours - 8760) >= 1:
                window_str = (
                    f"Indices 0\u2013{series.analysis_window_end} ({total_hours:g} h)"
                )
        elif (
            series.analysis_window_start is not None
            and series.analysis_window_end is not None
        ):
            window_str = (
                f"Indices {series.analysis_window_start}"
                f"\u2013{series.analysis_window_end}"
            )
    elif series.analysis_window_type:
        window_str = series.analysis_window_type.replace("_", " ").title()
    defaults.append(("Analysis window", window_str))
    fixed_electric = series.fixed_electric_heating or {}
    zones_replaced = fixed_electric.get("zones_replaced")
    rated_power = fixed_electric.get("rated_power_kw_per_zone")
    energy_supply_name = fixed_electric.get("energy_supply_name")
    if isinstance(zones_replaced, (int, float)) and zones_replaced > 0:
        heating_treatment = (
            f"Enabled - replaced heating in {int(zones_replaced)} zone(s)"
        )
        if isinstance(rated_power, (int, float)):
            heating_treatment += (
                f" with {float(rated_power):g} kW direct electric emitters"
            )
        if isinstance(energy_supply_name, str) and energy_supply_name:
            heating_treatment += f" on {energy_supply_name}"
        heating_treatment += "."
        defaults.append(("Heating system treatment", heating_treatment))
    extra_flags = [
        flag
        for flag in series.extra_flags
        if flag not in {"--future-homes-standard", "--future-homes-standard-FEE"}
    ]
    if extra_flags:
        defaults.append(("Additional engine flags", ", ".join(extra_flags)))

    parts.append("<h3>HEM configuration</h3>" + _dl(defaults))

    # --- 2. Perturbation approach ---
    offsets_str = (
        f"\u00b1{max(abs(o) for o in series.offsets_k):g} K ("
        + ", ".join(f"{o:+g}" for o in series.offsets_k)
        + ")"
        if series.offsets_k
        else "\u2014"
    )
    perturb: list[tuple[str, str]] = [
        (
            "Temperature offsets",
            f"{offsets_str} \u2014 applied to external temperature to "
            "derive the outputs-based HTC via perturbation-regression.",
        ),
        (
            "FIHC correction",
            ("Enabled" if series.fihc_enabled else "Disabled")
            + " \u2014 accounts for fabric internal heat capacity "
            "(thermal mass) when computing component-level HTC.",
        ),
    ]
    parts.append("<h3>Perturbation method</h3>" + _dl(perturb))

    # --- 3. Masking & filtering ---
    mask_items: list[tuple[str, str]] = []
    if settings.mask_block_hours is not None:
        mask_items.append(
            (
                "Block size",
                f"{settings.mask_block_hours:g} h \u2014 masking decisions are "
                "made at this granularity (e.g. keep/drop whole days).",
            )
        )
    if settings.valid_min_fraction is not None:
        pct = int(settings.valid_min_fraction * 100)
        mask_items.append(
            (
                "Valid fraction",
                f"{settings.valid_min_fraction:.2f} \u2014 a block is kept only "
                f"if \u2265{pct}% of its timesteps pass the per-timestep mask.",
            )
        )
    mask_items.append(
        (
            "Heating threshold",
            f"{settings.heating_eps_w:.1f} W \u2014 timesteps with space heating "
            "below this are excluded (unheated periods).",
        )
    )
    mask_items.append(
        (
            "Unmet demand",
            f"{settings.unmet_eps_kwh:.2f} kWh \u2014 timesteps where the heating "
            "system cannot meet demand are excluded.",
        )
    )
    parts.append(
        "<h3>Masking &amp; filtering</h3>"
        '<p class="section-intro">'
        "Masking filters to periods where the building is in a quasi-steady-state "
        "heating regime, comparable to co-heating test conditions.</p>"
        + _dl(mask_items)
    )

    # --- 4. Calibration (only for calibrate mode) ---
    cal = series.calibration
    if cal:
        cal_stat = cal.calibration_stat.upper()
        cal_block = cal.calibration_block.lower()
        cal_items: list[tuple[str, str]] = [
            (
                "Measured target",
                f"{cal.htc_target_w_per_k:.1f} W/K \u2014 the co-heating "
                "test measurement the model is being calibrated to match.",
            ),
            (
                "Baseline HTC",
                f"{cal.htc_baseline_w_per_k:.1f} W/K \u2014 the model's HTC "
                "before any calibration scalars are applied.",
            ),
        ]
        if cal.htc_calibrated_w_per_k is not None:
            residual_str = ""
            if cal.residual_w_per_k is not None:
                residual_str = f" (residual: {cal.residual_w_per_k:+.1f} W/K)"
            cal_items.append(
                (
                    "Calibrated HTC",
                    f"{cal.htc_calibrated_w_per_k:.1f} W/K{residual_str} \u2014 "
                    "after applying the calibration scalar.",
                )
            )
        cal_items.extend(
            [
                (
                    "Scalar found",
                    f"\u00d7{cal.scalar:.4f}",
                ),
                (
                    "Scalar bounds",
                    f"[{cal.scalar_bounds[0]:.2f}, {cal.scalar_bounds[1]:.2f}]",
                ),
                (
                    "Calibration metric",
                    f"masked {cal_block} {cal_stat} used for solver and headline metric.",
                ),
                (
                    "Regularisation \u03bb",
                    f"{cal.lambda_reg:.4f} \u2014 penalises large deviations "
                    "from unity to prevent over-fitting.",
                ),
            ]
        )

        ach_factor = series.env_overrides.get("HEM_ACH_CAL_FACTOR")
        if ach_factor:
            cal_items.append(
                (
                    "ACH cal factor",
                    f"{ach_factor} \u2014 multiplier applied to infiltration "
                    "rate during calibration runs.",
                )
            )
        if cal.fee_enabled:
            fee_text = "Enabled via --future-homes-standard-FEE"
            if cal.fee_status == "ok":
                fee_text += " - reports Fabric Energy Efficiency for baseline and calibrated models."
            elif cal.fee_status:
                fee_text += f" - status: {cal.fee_status}."
            else:
                fee_text += "."
            cal_items.append(("Postprocessing", fee_text))

        parts.append("<h3>Calibration</h3>" + _dl(cal_items))

    return "\n".join(parts)


def _build_definitions_html(
    values: dict[str, str],
    calibration_definition: str,
    extra_rows: list[tuple[str, str, str]] | None = None,
) -> str:
    """Build the metric definitions table with values shown in the Results section."""
    defs = [
        (
            INPUTS_BASED_HTC_LABEL,
            "HEM\u2019s design-stage heat loss from building inputs under fixed conditions. "
            "Uses a pressure-flow airflow model for ventilation, which differs from SAP\u2019s "
            "worksheet approach.",
        ),
        (
            OUTPUTS_BASED_UNFILTERED_LABEL,
            "Derived via perturbation-regression across multiple temperature-offset HEM runs. "
            "The model is treated as a black box: small temperature offsets (\u00b11 K) are "
            "applied, and the gradient of heat loss vs. perturbation yields the HTC "
            "before masking/filtering is applied.",
        ),
        (
            OUTPUTS_BASED_FILTERED_LABEL,
            calibration_definition,
        ),
    ]
    if extra_rows:
        defs.extend((name, desc) for name, desc, _ in extra_rows)
    extra_values = {name: value for name, _desc, value in (extra_rows or [])}
    merged_values = {**values, **extra_values}
    dash = "\u2014"
    rows = "".join(
        f'<tr><td class="def-metric">{_html_text(name)}</td>'
        f"<td>{_html_text(desc)}</td>"
        f'<td class="def-metric" style="text-align:right">'
        f"{_html_text(merged_values.get(name, dash))}</td></tr>"
        for name, desc in defs
    )
    return (
        '<table class="definitions-table"><thead>'
        "<tr><th>Metric</th><th>Definition</th><th>Value</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _build_representativeness_warnings(
    series: SummarySeries, settings: PlotSettings
) -> list[dict[str, str]]:
    """Compute report warnings when analysis may be non-representative."""
    warnings: list[dict[str, str]] = []

    # --- Winter coverage (Nov-Feb), measured on kept masked timesteps ---
    abs_indices = list(series.selected_indices)
    if not abs_indices:
        n = len(series.q_loss_by_offset.get("0.0", []))
        start = series.analysis_window_start or 0
        abs_indices = [start + i for i in range(n)]
    mask, _mask_error = _mask_inputs(series, settings)
    if abs_indices and mask and series.timestep_hours > 0:
        n_common = min(len(abs_indices), len(mask))
        if n_common <= 0:
            return warnings
        abs_indices = abs_indices[:n_common]
        kept_indices = [idx for idx, keep in zip(abs_indices, mask[:n_common]) if keep]
    else:
        kept_indices = []

    if kept_indices and series.timestep_hours > 0:
        steps_per_day = int(round(24.0 / series.timestep_hours))
        if steps_per_day > 0:
            winter_flags = [
                ((idx // steps_per_day) % 365 >= 304)
                or ((idx // steps_per_day) % 365 < 59)
                for idx in kept_indices
            ]
            winter_fraction = sum(1 for f in winter_flags if f) / len(kept_indices)
            severity = ""
            if winter_fraction < 0.40:
                severity = "high"
            elif winter_fraction < 0.55:
                severity = "medium"
            if severity:
                warnings.append(
                    {
                        "id": "low-winter-coverage",
                        "severity": severity,
                        "title": "Low winter coverage",
                        "detected": (
                            f"Winter coverage is {winter_fraction * 100.0:.1f}% of kept "
                            "masked timesteps (Nov-Feb)."
                        ),
                        "impact": (
                            "HTC may be driven by non-winter conditions and less "
                            "representative of heating-season performance."
                        ),
                        "fix": (
                            "Review masking and unmet-demand behavior; ensure winter "
                            "heating periods are retained in the analysed data."
                        ),
                    }
                )

    # --- Unmet-demand prevalence ---
    unmet_raw = series.base_results_series.get(
        "_unmet_demand: Living"
    ) or series.base_results_series.get("_unmet_demand: total")
    unmet_values: list[float] = []
    if isinstance(unmet_raw, dict) and isinstance(unmet_raw.get("values"), list):
        unmet_values = [float(v) for v in unmet_raw["values"]]
    if unmet_values:
        unmet_over = [u for u in unmet_values if u > settings.unmet_eps_kwh]
        unmet_frac = len(unmet_over) / len(unmet_values)
        severity = ""
        if unmet_frac >= 0.10:
            severity = "high"
        elif unmet_frac >= 0.03:
            severity = "medium"
        if severity:
            warnings.append(
                {
                    "id": "high-unmet-demand",
                    "severity": severity,
                    "title": "High unmet-demand prevalence",
                    "detected": (
                        f"{unmet_frac * 100.0:.1f}% of selected timesteps exceed unmet "
                        f"threshold ({settings.unmet_eps_kwh:.2f} kWh)."
                    ),
                    "impact": (
                        "Many timesteps are excluded by masking; masked HTC may "
                        "under-represent high-load winter behavior."
                    ),
                    "fix": (
                        "Increase heating system capacity in the model and rerun, then "
                        "check unmet-demand and kept-bin diagnostics."
                    ),
                }
            )

    return warnings


def _build_warnings_section_html(warnings: list[dict[str, str]]) -> str:
    if not warnings:
        return ""
    cards = "".join(
        (
            '<article class="warning-card">'
            f'<div class="warning-badge warning-{_html_attr(w["severity"])}">'
            f"{_html_text(w['severity'].upper())}</div>"
            f"<h3>{_html_text(w['title'])}</h3>"
            f"<p><strong>Detected:</strong> {_html_text(w['detected'])}</p>"
            f"<p><strong>Why this matters:</strong> {_html_text(w['impact'])}</p>"
            f"<p><strong>Suggested fix:</strong> {_html_text(w['fix'])}</p>"
            "</article>"
        )
        for w in warnings
    )
    return (
        '<section id="warnings" class="prose-section">'
        "<h2>Warnings</h2>"
        '<p class="section-intro">'
        "These flags indicate conditions where the reported HTC values may be less "
        "representative of true year-round performance."
        "</p>"
        f"{cards}"
        "</section>"
    )


def write_plotly_html_report(
    series_list: list[SummarySeries], output_path: Path, settings: PlotSettings
) -> None:
    _, pio, _ = _plotly_imports()
    raw_stem = series_list[0].path.stem if series_list else ""
    report_title = raw_stem.removesuffix("_htc_summary").replace("_", " ")
    first = series_list[0] if series_list else None
    is_calibrate = first is not None and first.calibration is not None
    mode_label = "Calibrate" if is_calibrate else "Run"
    nav_warnings = _build_representativeness_warnings(first, settings) if first else []

    metrics = _weekly_masked_component_summary(first, settings) if first else {}

    def _fmt(v: object, digits: int = 1) -> str:
        if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            return "\u2014"
        return f"{float(v):.{digits}f}"

    headline_htc = _fmt(metrics.get("weekly_masked_mean_w_per_k"))
    if is_calibrate and first and first.calibration:
        headline_htc = _fmt(first.calibration.htc_calibrated_w_per_k)
    headline_label = OUTPUTS_BASED_FILTERED_LABEL
    target_html = ""
    if is_calibrate and first and first.calibration:
        target_html = (
            f'<span class="header-target">Target: '
            f"{first.calibration.htc_target_w_per_k:.1f} W/K</span>"
        )

    include_js_used = False

    def _render_figs(figs: list, section_prefix: str) -> str:
        nonlocal include_js_used
        parts = []
        for i, fig in enumerate(figs):
            meta = dict(fig.layout.meta) if isinstance(fig.layout.meta, dict) else {}
            meta["fig_id"] = f"{section_prefix}-fig-{i + 1}"
            fig.update_layout(meta=meta)
            js = "cdn" if not include_js_used else False
            html = _render_figure(fig, pio, js)
            if js == "cdn":
                include_js_used = True
            parts.append(html)
        return "".join(parts)

    all_section_html: list[str] = []

    for series in series_list:
        baseline_series = _load_calibration_baseline_series(series)
        figs = build_plotly_figures(series, settings, baseline_series=baseline_series)
        by_section: dict[str, list] = {
            SECTION_RESULTS: [],
            SECTION_BREAKDOWNS: [],
            SECTION_DATA_QUALITY: [],
        }
        for fig in figs:
            meta = fig.layout.meta if isinstance(fig.layout.meta, dict) else {}
            sec = meta.get("section", SECTION_RESULTS)
            by_section.setdefault(sec, []).append(fig)

        # --- Background section ---
        background_html = (
            '<section id="background" class="prose-section">'
            "<h2>Background</h2>"
            '<div class="prose-body">'
            "<p><strong>H3 (HEM HTC Harness)</strong> is a Python CLI tool that computes and analyses "
            "the Heat Transfer Coefficient (HTC) of a dwelling from Home Energy Model outputs, "
            "and optionally calibrates against measured values. "
            '<a href="https://github.com/vulcan-energy/hem-htc-harness/" target="_blank">'
            "github.com/vulcan-energy/hem-htc-harness</a></p>"
            "<h3>SAP vs HEM</h3>"
            "<p>The Standard Assessment Procedure (SAP) is the current UK methodology for assessing the "
            "energy performance of dwellings. SAP uses a monthly calculation with worksheet-based rules "
            "to estimate heat loss, producing a single HTC figure.</p>"
            "<p>The Home Energy Model (HEM) is the government\u2019s replacement for SAP \u2014 an open-source, "
            "sub-hourly calculation that solves a zone heat balance (following BS EN ISO 52016-1:2017). "
            "HEM\u2019s greater granularity brings two practical advantages for HTC work:</p>"
            "<ul>"
            "<li>The outputs-based HTC derived from HEM lets measured-HTC providers explicitly specify "
            "assumptions about wind, solar gains, and thermal mass.</li>"
            "<li>Multiple granular residuals can be compared with HEM (vs. a single figure with SAP), "
            "enabling more identifiable explanations of discrepancies.</li>"
            "</ul>"
            "<h3>What is HTC?</h3>"
            "<p>The Heat Transfer Coefficient (HTC) measures how quickly a building loses heat to the outside, "
            "in watts per degree of temperature difference (W/K). A lower HTC means a better-insulated building. "
            "HTC can be decomposed into three pathways:</p>"
            "<ul>"
            "<li><strong>Fabric</strong> &mdash; heat conducted through walls, roof, floor, windows</li>"
            "<li><strong>Thermal bridges</strong> &mdash; additional losses at junctions between building elements</li>"
            "<li><strong>Ventilation</strong> &mdash; heat lost through air movement (infiltration, mechanical ventilation)</li>"
            "</ul>"
            "<p>H3 reports three headline HTC metrics: one inputs-based metric and two "
            "outputs-based metrics (unfiltered and filtered). It also compares two "
            "outputs-based calculation methods \u2014 regression and division \u2014 to help "
            "interpret model behaviour and calibration performance.</p>"
            "</div></section>"
        )
        all_section_html.append(background_html)

        # --- Results section: compute metric values for the table ---
        metric_values: dict[str, str] = {
            INPUTS_BASED_HTC_LABEL: (
                f"{_fmt(series.htc_design)} W/K"
                if series.htc_design is not None
                else "\u2014"
            ),
            OUTPUTS_BASED_UNFILTERED_LABEL: (
                f"{_fmt(series.htc_empirical)} W/K"
                if series.htc_empirical is not None
                else "\u2014"
            ),
            OUTPUTS_BASED_FILTERED_LABEL: "\u2014",
        }
        if series.calibration:
            static_from = series.calibration.htc_static_baseline_w_per_k
            static_to = series.calibration.htc_static_calibrated_w_per_k
            if static_from is not None and static_to is not None:
                static_delta = static_to - static_from
                metric_values[INPUTS_BASED_HTC_LABEL] = (
                    f"{static_from:.1f} \u2192 {static_to:.1f} (\u0394 {static_delta:+.1f}) W/K"
                )

            empirical_from = series.calibration.htc_empirical_baseline_w_per_k
            empirical_to = series.calibration.htc_empirical_calibrated_w_per_k
            if empirical_from is not None and empirical_to is not None:
                empirical_delta = empirical_to - empirical_from
                metric_values[OUTPUTS_BASED_UNFILTERED_LABEL] = (
                    f"{empirical_from:.1f} \u2192 {empirical_to:.1f} (\u0394 {empirical_delta:+.1f}) W/K"
                )

            cal_from = series.calibration.htc_baseline_w_per_k
            cal_to = series.calibration.htc_calibrated_w_per_k
            if cal_to is not None:
                cal_delta = cal_to - cal_from
                metric_values[OUTPUTS_BASED_FILTERED_LABEL] = (
                    f"{cal_from:.1f} \u2192 {cal_to:.1f} (\u0394 {cal_delta:+.1f}) W/K"
                )
            else:
                metric_values[OUTPUTS_BASED_FILTERED_LABEL] = f"{cal_from:.1f} W/K"
        elif headline_htc != "\u2014":
            metric_values[OUTPUTS_BASED_FILTERED_LABEL] = f"{headline_htc} W/K"
        if series.calibration:
            cal_stat = series.calibration.calibration_stat.upper()
            cal_block = series.calibration.calibration_block.lower()
            calibration_definition = (
                f"The masked, {cal_block}-aggregated outputs-based HTC from the regression "
                "method. Only heated "
                "timesteps with no unmet demand are included and low-validity blocks "
                f"are dropped. The headline and solver use the {cal_stat} of the "
                "remaining block HTCs."
            )
        else:
            calibration_definition = (
                "The masked, policy-driven outputs-based HTC from the regression "
                "method. This is the selected headline metric used for filtering-aware "
                "comparisons."
            )
        extra_rows: list[tuple[str, str, str]] = []
        if series.calibration:
            fee_base = series.calibration.fee_baseline_kwh_per_m2_yr
            fee_cal = series.calibration.fee_calibrated_kwh_per_m2_yr
            if fee_base is not None and fee_cal is not None:
                fee_delta = fee_cal - fee_base
                extra_rows.append(
                    (
                        "FEE (kWh/m2.yr)",
                        "Fabric Energy Efficiency from HEM FEE postprocessing, "
                        "reported for the baseline and final calibrated models.",
                        f"{fee_base:.1f} \u2192 {fee_cal:.1f} (\u0394 {fee_delta:+.1f})",
                    )
                )
        definitions_table = _build_definitions_html(
            metric_values, calibration_definition, extra_rows=extra_rows
        )
        results_figs_html = _render_figs(by_section[SECTION_RESULTS], "results")
        results_html = (
            '<section id="results">'
            "<h2>Results</h2>"
            '<div class="prose-body">'
            f"{definitions_table}"
            "<h3>How the metrics are extracted</h3>"
            f"<p>The <strong>{INPUTS_BASED_HTC_LABEL}</strong> is HEM\u2019s design-stage output: a single number "
            "calculated from the building description under fixed conditions. It does not "
            "use dynamic simulation. Note that HEM\u2019s static calculation uses a different "
            "ventilation method to SAP, so the two are not directly comparable.</p>"
            "<p>Each outputs-based estimate uses a base run plus \u00b11\u00b0C temperature-offset "
            "runs. In calibration mode, H3 repeats this perturbation batch for the "
            "baseline, global and any iterative calibration steps. "
            f"The <strong>{OUTPUTS_BASED_DIVISION_LABEL}</strong> reads HEM\u2019s own internal "
            "heat-balance accounting from the base run and divides by the temperature "
            f"difference at each timestep. The <strong>{OUTPUTS_BASED_REGRESSION_LABEL}</strong> instead "
            "measures how total heat loss <em>changes</em> across the three temperature "
            "shifts \u2014 a perturbation approach analogous to a physical co-heating test. "
            "Unless a label explicitly says division method, outputs-based HTC in this "
            "report refers to the perturbation-regression method. "
            f"The <strong>{OUTPUTS_BASED_UNFILTERED_LABEL}</strong> is this regression method "
            "before masking. "
            f"The <strong>{OUTPUTS_BASED_FILTERED_LABEL}</strong> applies the same regression method, "
            "but only looks at periods when heating is on and can keep up with demand \u2014 "
            "filtering out summer and undersized periods to give a more stable, "
            "measurement-comparable value. This filtered regression metric is the one "
            "ultimately used as the headline comparison metric.</p>"
            "<h3>How to read the headline chart</h3>"
            "<p>Each bar shows the total HTC, broken down by where heat escapes: "
            "<strong>fabric</strong> (walls, roof, windows), <strong>thermal bridges</strong> "
            "(junctions between elements), and <strong>ventilation</strong> (air exchange). "
            "The bars let you compare the three headline metrics at a glance. "
            "In calibrate mode, the dashed target line shows the measured HTC \u2014 if "
            f"the {OUTPUTS_BASED_FILTERED_LABEL} bar is close to the target, the model matches "
            "reality well.</p>"
            "</div>"
            f"{results_figs_html}"
            "</section>"
        )
        all_section_html.append(results_html)
        if series is first and nav_warnings:
            all_section_html.append(_build_warnings_section_html(nav_warnings))

        # --- Assumptions section ---
        assumptions_table = _build_assumptions_html(series, settings)
        assumptions_html = (
            '<section id="assumptions" class="prose-section">'
            "<h2>Assumptions</h2>"
            '<div class="prose-body">'
            "<p><strong>Why these assumptions matter:</strong> The heating schedule is "
            "disabled so the building is heated 24/7 \u2014 this isolates fabric "
            "performance from occupancy patterns. Masking then filters to periods where "
            "the building is in a quasi-steady-state regime, comparable to co-heating "
            "test conditions.</p>"
            f"{assumptions_table}"
            "</div></section>"
        )
        all_section_html.append(assumptions_html)

        # --- Breakdowns section ---
        breakdown_figs_html = _render_figs(by_section[SECTION_BREAKDOWNS], "breakdowns")
        breakdowns_html = (
            '<section id="breakdowns" class="section-appendix">'
            "<h2>Breakdowns</h2>"
            '<p class="section-intro">'
            "These charts decompose the headline metrics in more detail, comparing "
            "the outputs-based regression and division methods across the year. In calibrate mode, the "
            "waterfall shows how scalars bridge the gap to the measured target."
            "</p>"
            f"{breakdown_figs_html}"
            "</section>"
        )
        all_section_html.append(breakdowns_html)

        # --- Data Quality section ---
        dq_figs_html = _render_figs(by_section[SECTION_DATA_QUALITY], "data-quality")
        dq_html = (
            '<section id="data-quality" class="section-appendix">'
            "<h2>Data Quality</h2>"
            '<p class="section-intro">'
            "These charts validate the data quality and methodology. They show which "
            "time periods are included in the analysis, whether the regression is "
            "well-behaved, and how the metric stabilises at different aggregation levels."
            "</p>"
            f"{dq_figs_html}"
            "</section>"
        )
        all_section_html.append(dq_html)

    # Build nav sublinks from all figures
    nav_groups = {
        "Background": [("background", "Background")],
        "Results": [("results", "Results")],
        **({"Warnings": [("warnings", "Warnings")]} if nav_warnings else {}),
        "Assumptions": [("assumptions", "Assumptions")],
        "Breakdowns": [("breakdowns", "Breakdowns")],
        "Data Quality": [("data-quality", "Data Quality")],
    }
    section_pills = "".join(
        f'<a class="nav-pill" href="#{entries[0][0]}">{group}</a>'
        for group, entries in nav_groups.items()
    )

    body_content = "\n".join(all_section_html)
    html = (
        "<!doctype html>\n<html>\n<head>\n"
        '  <meta charset="utf-8" />\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        "  <title>H3 Report</title>\n"
        "  <style>\n"
        f"    :root {{ --vulcan-bg: {PAPER_BG}; --vulcan-ink: {TEXT_COLOR}; "
        "  --vulcan-accent: #EAFD5A; --vulcan-accent-soft: #B7CEC4; "
        "  --vulcan-border: rgba(26, 52, 55, 0.12); }\n"
        "    html { scroll-behavior: smooth; }\n"
        f"    body {{ font-family: {PLOT_FONT}; margin: 0; padding: 0; "
        "  background: var(--vulcan-bg); color: var(--vulcan-ink); }\n"
        "    .report-header { position: sticky; top: 0; z-index: 20; "
        "  display: flex; align-items: center; gap: 16px; padding: 14px 28px; "
        f"  background: {TEXT_COLOR}; color: #fff; font-size: 14px; "
        "  box-shadow: 0 2px 8px rgba(0,0,0,0.15); }\n"
        "    .report-header .report-title { font-weight: 800; font-size: 16px; }\n"
        "    .mode-badge { padding: 2px 10px; border-radius: 999px; font-size: 11px; "
        "  font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; }\n"
        "    .mode-run { background: #4E79A7; color: #fff; }\n"
        "    .mode-calibrate { background: #4ECDC4; color: #1A3437; }\n"
        "    .header-metric { margin-left: auto; font-weight: 600; }\n"
        "    .header-target { color: #FF6B6B; font-weight: 600; margin-left: 12px; }\n"
        "    .section-nav { position: sticky; top: 48px; z-index: 19; "
        "  display: flex; flex-wrap: wrap; gap: 8px; "
        "  padding: 10px 28px; margin: 0; "
        "  background: var(--vulcan-bg); "
        "  border-bottom: 1px solid var(--vulcan-border); }\n"
        "    .nav-pill { display: inline-flex; align-items: center; "
        "  padding: 6px 14px; border-radius: 999px; "
        "  background: rgba(26, 52, 55, 0.06); color: var(--vulcan-ink); "
        "  text-decoration: none; font-size: 13px; font-weight: 600; }\n"
        "    .nav-pill:hover { background: rgba(26, 52, 55, 0.14); }\n"
        "    .warning-card { margin: 10px 0; padding: 12px 14px; "
        "  border: 1px solid var(--vulcan-border); border-radius: 8px; "
        "  background: #fff; }\n"
        "    .warning-card h3 { margin: 6px 0 8px 0; }\n"
        "    .warning-card p { margin: 4px 0; font-size: 13px; color: #374151; }\n"
        "    .warning-badge { display: inline-block; padding: 2px 8px; border-radius: 999px; "
        "  font-size: 10px; font-weight: 700; letter-spacing: 0.04em; }\n"
        "    .warning-high { background: #FF6B6B; color: #fff; }\n"
        "    .warning-medium { background: #F59E0B; color: #111827; }\n"
        "    .content { max-width: 1100px; margin: 0 auto; padding: 16px 24px 48px 24px; }\n"
        "    section { margin-bottom: 48px; padding: 24px 28px; "
        "  background: #fff; border: 1px solid var(--vulcan-border); "
        "  border-radius: 12px; box-shadow: 0 2px 8px rgba(26, 52, 55, 0.06); "
        "  scroll-margin-top: 100px; }\n"
        "    section:last-of-type { margin-bottom: 24px; }\n"
        "    .section-appendix { background: #FAFCFB; }\n"
        "    h2 { margin: 0 0 12px 0; font-size: 22px; font-weight: 800; "
        "  letter-spacing: -0.01em; }\n"
        "    h3 { margin: 16px 0 6px 0; font-size: 16px; font-weight: 700; }\n"
        "    .prose-body { max-width: 720px; font-size: 14px; line-height: 1.6; "
        "  color: #374151; }\n"
        "    .prose-body p { margin: 0 0 10px 0; }\n"
        "    .prose-body ul { margin: 4px 0 12px 0; padding-left: 20px; }\n"
        "    .prose-body li { margin-bottom: 4px; }\n"
        "    .section-intro { font-size: 14px; color: #6b7280; "
        "  margin: 0 0 16px 0; max-width: 720px; }\n"
        "    .definitions-table { width: 100%; border-collapse: collapse; "
        "  margin: 12px 0 20px 0; font-size: 13px; }\n"
        "    .definitions-table th { text-align: left; padding: 8px 12px; "
        "  background: rgba(26, 52, 55, 0.04); border-bottom: 2px solid var(--vulcan-border); "
        "  font-weight: 700; }\n"
        "    .definitions-table td { padding: 8px 12px; "
        "  border-bottom: 1px solid var(--vulcan-border); vertical-align: top; }\n"
        "    .definitions-table .def-metric { font-weight: 700; white-space: nowrap; width: 140px; }\n"
        "    .assumptions-table { width: auto; border-collapse: collapse; "
        "  margin: 12px 0; font-size: 13px; }\n"
        "    .assumptions-table th { text-align: left; padding: 6px 16px 6px 0; "
        "  border-bottom: 2px solid var(--vulcan-border); font-weight: 700; }\n"
        "    .assumptions-table td { padding: 6px 16px 6px 0; "
        "  border-bottom: 1px solid var(--vulcan-border); }\n"
        "    .report-figure { scroll-margin-top: 100px; max-width: 1050px; }\n"
        "    .figure-with-side-metrics { display: grid; grid-template-columns: 220px 1fr; "
        "  gap: 14px; align-items: stretch; min-height: var(--side-metrics-height, 600px); }\n"
        "    .side-metrics-column { display: grid; grid-template-rows: 1fr 1fr; gap: 12px; "
        "  min-height: var(--side-metrics-height, 600px); }\n"
        "    .side-metric-card { border: 1px solid var(--vulcan-border); border-radius: 10px; "
        "  padding: 12px; background: #fff; display: flex; flex-direction: column; "
        "  justify-content: center; }\n"
        "    .side-metric-label { font-size: 12px; font-weight: 700; color: #4b5563; }\n"
        "    .side-metric-value { font-size: 30px; line-height: 1.0; font-weight: 800; "
        "  color: var(--vulcan-ink); margin-top: 6px; }\n"
        "    .side-metric-unit { font-size: 12px; color: #6b7280; margin-top: 4px; }\n"
        "    .side-metrics-chart { min-width: 0; }\n"
        "    .figure-header { display: flex; flex-wrap: wrap; "
        "  justify-content: flex-start; align-items: flex-start; "
        "  gap: 10px; margin: 16px 0 8px 0; }\n"
        "    .figure-title { font-size: 18px; font-weight: 800; "
        "  color: var(--vulcan-ink); letter-spacing: -0.01em; }\n"
        "    .figure-meta { display: flex; flex-wrap: wrap; gap: 8px; "
        "  align-items: flex-start; }\n"
        "    .stats-box { max-width: 520px; padding: 6px 8px; "
        "  font-size: 11px; color: #4b5563; background: #fff; "
        "  border: 1px solid var(--vulcan-border); border-radius: 6px; }\n"
        "    .stats-columns { column-count: 3; column-gap: 16px; max-width: 620px; }\n"
        "    code { background: rgba(26, 52, 55, 0.06); padding: 2px 6px; "
        "  border-radius: 4px; color: var(--vulcan-ink); font-size: 12px; }\n"
        "    a { color: #2563EB; }\n"
        "    @media print { .report-header { position: static; } "
        "  .section-nav { display: none; } }\n"
        "  </style>\n"
        "</head>\n<body>\n"
        '  <header class="report-header">\n'
        f'    <span class="report-title">H3 Report: {_html_text(report_title)}</span>\n'
        f'    <span class="mode-badge mode-{_html_attr(mode_label.lower())}">'
        f"{_html_text(mode_label)}</span>\n"
        f'    <span class="header-metric">{_html_text(headline_label)}: '
        f"{_html_text(headline_htc)} W/K</span>\n"
        f"    {target_html}\n"
        "  </header>\n"
        f'  <nav class="section-nav">{section_pills}</nav>\n'
        f'  <div class="content">{body_content}</div>\n'
        "</body>\n</html>"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)


def write_plotly_report_and_open(
    series_list: list[SummarySeries], output_path: Path, settings: PlotSettings
) -> None:
    write_plotly_html_report(series_list, output_path, settings)
    try:
        webbrowser.open(output_path.resolve().as_uri())
    except Exception:
        pass
