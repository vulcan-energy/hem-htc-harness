# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import htc_harness.plotting.report as report_mod
from htc_harness.plotting.figures import _fig_headline_stacked_bar
from htc_harness.plotting.loader import load_summary_series
from htc_harness.plotting.models import CalibrationData, PlotSettings, SummarySeries


def _make_series(
    *,
    n: int = 400,
    selected_indices: list[int] | None = None,
    unmet_values: list[float] | None = None,
) -> SummarySeries:
    if selected_indices is None:
        selected_indices = list(range(n))
    if unmet_values is None:
        unmet_values = [0.0] * n
    return SummarySeries(
        path=Path("dummy_summary.json"),
        offsets_k=[0.0, 1.0],
        timestep_hours=0.5,
        htc_empirical=None,
        htc_empirical_masked=None,
        htc_design=None,
        q_loss_by_offset={"0.0": [1000.0] * n, "1.0": [900.0] * n},
        q_loss_components_by_offset={},
        q_loss_by_offset_by_source={},
        q_system_total_by_offset={"0.0": [1000.0] * n},
        q_system_fabric_int_by_offset={},
        hem_htc_components_by_offset={},
        base_results_series={"_unmet_demand: total": {"values": unmet_values}},
        month_bins=[],
        air_node_energy_balance={},
        internal_air_temp_c=[],
        external_air_temp_c=[],
        wind_speed_ms=[],
        wind_direction_deg=[],
        htc_by_source={},
        htc_mean_by_source={},
        htc_primary_source="",
        base_results_dir=None,
        ventilation=None,
        calibration=None,
        mode="run",
        engine_runner=None,
        engine_commit=None,
        env_overrides={},
        extra_flags=[],
        analysis_window_type="index",
        analysis_window_start=0,
        analysis_window_end=max(selected_indices) + 1 if selected_indices else n,
        selected_indices=selected_indices,
        fihc_enabled=False,
    )


def _write_static_csv(
    path: Path,
    *,
    fabric: float,
    bridges: float,
    ventilation: float,
    total: float,
) -> None:
    path.write_text(
        "\n".join(
            [
                f"Fabric heat transfer coefficient,W / K,{fabric}",
                f"Thermal bridges heat transfer coefficient,W / K,{bridges}",
                f"Ventilation heat transfer coefficient,W / K,{ventilation}",
                f"Heat transfer coefficient,W / K,{total}",
            ]
        )
        + "\n"
    )


def test_warnings_low_winter_coverage_thresholds() -> None:
    settings = PlotSettings(valid_min_fraction=0.95, unmet_eps_kwh=0.0)
    steps_per_day = 48

    # High: 0% winter (all summer day index 200).
    sel_high = [200 * steps_per_day + i for i in range(240)]
    high_series = _make_series(n=len(sel_high), selected_indices=sel_high)
    high = report_mod._build_representativeness_warnings(high_series, settings)
    high_winter = next(w for w in high if w["id"] == "low-winter-coverage")
    assert high_winter["severity"] == "high"

    # Medium: 50% winter.
    winter = [10 * steps_per_day + i for i in range(120)]
    non_winter = [200 * steps_per_day + i for i in range(120)]
    sel_medium = winter + non_winter
    medium_series = _make_series(n=len(sel_medium), selected_indices=sel_medium)
    medium = report_mod._build_representativeness_warnings(medium_series, settings)
    med_winter = next(w for w in medium if w["id"] == "low-winter-coverage")
    assert med_winter["severity"] == "medium"

    # None: 60% winter.
    winter60 = [10 * steps_per_day + i for i in range(180)]
    non_winter40 = [200 * steps_per_day + i for i in range(120)]
    sel_none = winter60 + non_winter40
    none_series = _make_series(n=len(sel_none), selected_indices=sel_none)
    none = report_mod._build_representativeness_warnings(none_series, settings)
    assert all(w["id"] != "low-winter-coverage" for w in none)


def test_warnings_unmet_prevalence_thresholds() -> None:
    settings = PlotSettings(valid_min_fraction=0.95, unmet_eps_kwh=0.0)
    n = 100

    high = _make_series(unmet_values=[1.0] * 15 + [0.0] * (n - 15))
    high_warnings = report_mod._build_representativeness_warnings(high, settings)
    high_unmet = next(w for w in high_warnings if w["id"] == "high-unmet-demand")
    assert high_unmet["severity"] == "high"

    medium = _make_series(unmet_values=[1.0] * 5 + [0.0] * (n - 5))
    med_warnings = report_mod._build_representativeness_warnings(medium, settings)
    med_unmet = next(w for w in med_warnings if w["id"] == "high-unmet-demand")
    assert med_unmet["severity"] == "medium"

    none = _make_series(unmet_values=[1.0] * 2 + [0.0] * (n - 2))
    none_warnings = report_mod._build_representativeness_warnings(none, settings)
    assert all(w["id"] != "high-unmet-demand" for w in none_warnings)


def test_warnings_section_html_contains_detected_values() -> None:
    warnings = [
        {
            "id": "high-unmet-demand",
            "severity": "medium",
            "title": "High unmet-demand prevalence",
            "detected": "7.2% of selected timesteps exceed unmet threshold (0.00 kWh).",
            "impact": "Many timesteps are excluded by masking.",
            "fix": "Increase heating system capacity.",
        }
    ]
    html = report_mod._build_warnings_section_html(warnings)
    assert 'section id="warnings"' in html
    assert "High unmet-demand prevalence" in html
    assert "7.2% of selected timesteps exceed unmet threshold" in html


def test_render_figure_escapes_metadata_and_preserves_plotly_fragment() -> None:
    fig = SimpleNamespace(
        layout=SimpleNamespace(
            meta={
                "fig_id": 'fig" onmouseover="alert(1)',
                "title": '<img src=x onerror="alert(1)">',
                "stats": 'Safe<br><svg onload="alert(1)">',
                "stats_class": 'stats-box" onclick="alert(1)',
            },
            height=400,
        )
    )

    class _FakePio:
        @staticmethod
        def to_html(
            fig, full_html: bool = False, include_plotlyjs: str | bool = False
        ) -> str:
            return '<div class="plotly-chart"></div>'

    rendered = report_mod._render_figure(fig, _FakePio(), False)

    assert '<div class="plotly-chart"></div>' in rendered
    assert "<img" not in rendered
    assert "<svg" not in rendered
    assert "&lt;img src=x onerror=" in rendered
    assert "Safe<br>&lt;svg onload=" in rendered
    assert 'id="fig&quot; onmouseover=&quot;alert(1)"' in rendered
    assert 'class="stats-box&quot; onclick=&quot;alert(1)"' in rendered


def test_assumptions_escape_summary_metadata() -> None:
    series = replace(
        _make_series(),
        engine_runner='<img src=x onerror="alert(1)">',
        engine_commit="</code><script>alert(1)</script>",
        analysis_window_type='<svg onload="alert(1)">',
        env_overrides={"HEM_ACH_CAL_FACTOR": "</td><script>alert(2)</script><td>"},
        extra_flags=['--flag=<img src=x onerror="alert(3)">'],
        fixed_electric_heating={
            "zones_replaced": 1,
            "energy_supply_name": '<svg onload="alert(4)">',
        },
        calibration=replace(
            CalibrationData(
                htc_target_w_per_k=100.0,
                htc_baseline_w_per_k=90.0,
                htc_calibrated_w_per_k=95.0,
                residual_w_per_k=5.0,
                scalar=1.1,
                s_global=0.5,
                monthly_labels=[],
                monthly_baseline=[],
                monthly_global=[],
                monthly_calibrated=[],
                monthly_mask_reason="",
                masking_heating_eps_w=0.0,
                masking_unmet_eps_kwh=0.0,
                masking_valid_min_fraction=0.95,
                masking_mask_block_hours=24.0,
                delta_scalar=0.2,
                calibration_stat="mean",
                calibration_block="monthly",
                lambda_reg=0.01,
                scalar_bounds=(0.1, 2.5),
            ),
            fee_status='<img src=x onerror="alert(5)">',
            fee_enabled=True,
        ),
    )

    html = report_mod._build_assumptions_html(series, PlotSettings())

    assert "<script" not in html
    assert "<img" not in html
    assert "<svg" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=" in html
    assert "&lt;svg onload=" in html


def test_definitions_escape_dynamic_rows() -> None:
    html = report_mod._build_definitions_html(
        {},
        '<img src=x onerror="alert(1)">',
        extra_rows=[
            (
                "<script>alert(1)</script>",
                '<svg onload="alert(1)">',
                '<a href="javascript:alert(1)">value</a>',
            )
        ],
    )

    assert "<script" not in html
    assert "<svg" not in html
    assert "<a href=" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;svg onload=" in html
    assert "&lt;a href=" in html


def test_warnings_section_escapes_text_and_severity_attribute() -> None:
    html = report_mod._build_warnings_section_html(
        [
            {
                "id": "warning",
                "severity": 'high" onclick="alert(1)',
                "title": '<img src=x onerror="alert(2)">',
                "detected": "<script>alert(3)</script>",
                "impact": '<svg onload="alert(4)">',
                "fix": '<a href="javascript:alert(5)">fix</a>',
            }
        ]
    )

    assert "<script" not in html
    assert "<img" not in html
    assert "<svg" not in html
    assert "<a href=" not in html
    assert "warning-high&quot; onclick=&quot;alert(1)" in html
    assert "&lt;script&gt;alert(3)&lt;/script&gt;" in html


def test_write_report_renders_warnings_nav_and_section(
    monkeypatch, tmp_path: Path
) -> None:
    series = _make_series(unmet_values=[1.0] * 8 + [0.0] * 92)
    settings = PlotSettings(valid_min_fraction=0.95, unmet_eps_kwh=0.0)

    class _FakePio:
        @staticmethod
        def to_html(
            fig, full_html: bool = False, include_plotlyjs: str | bool = False
        ) -> str:
            return "<div>fake-chart</div>"

    monkeypatch.setattr(report_mod, "_plotly_imports", lambda: (None, _FakePio(), None))
    monkeypatch.setattr(
        report_mod, "build_plotly_figures", lambda _s, _st, **_kwargs: []
    )

    out = tmp_path / "report.html"
    report_mod.write_plotly_html_report([series], out, settings)
    html = out.read_text()
    assert '<a class="nav-pill" href="#results">Results</a>' in html
    assert '<a class="nav-pill" href="#warnings">Warnings</a>' in html
    assert html.index('href="#results"') < html.index('href="#warnings"')
    assert 'section id="warnings"' in html
    assert html.index('section id="results"') < html.index('section id="warnings"')


def test_write_report_escapes_filename_derived_title(
    monkeypatch, tmp_path: Path
) -> None:
    series = replace(
        _make_series(),
        path=tmp_path / '<img src=x onerror="alert(1)">_htc_summary.json',
    )
    settings = PlotSettings(valid_min_fraction=0.95, unmet_eps_kwh=0.0)

    class _FakePio:
        @staticmethod
        def to_html(
            fig, full_html: bool = False, include_plotlyjs: str | bool = False
        ) -> str:
            return '<div class="plotly-chart"></div>'

    monkeypatch.setattr(report_mod, "_plotly_imports", lambda: (None, _FakePio(), None))
    monkeypatch.setattr(
        report_mod, "build_plotly_figures", lambda _s, _st, **_kwargs: []
    )

    out = tmp_path / "report.html"
    report_mod.write_plotly_html_report([series], out, settings)
    html = out.read_text()

    assert 'H3 Report: &lt;img src=x onerror="alert(1)"&gt;' in html
    assert "<img src=x" not in html


def test_plotly_fragment_json_does_not_emit_script_terminator() -> None:
    go, pio, _ = report_mod._plotly_imports()
    payload = '</script><script id="injected">alert(1)</script>'
    fig = go.Figure(go.Bar(name=payload))

    fragment = pio.to_html(fig, full_html=False, include_plotlyjs=False)

    assert payload not in fragment
    assert '<script id="injected">' not in fragment


def test_write_report_uses_reframed_htc_labels(monkeypatch, tmp_path: Path) -> None:
    series = _make_series()
    settings = PlotSettings(valid_min_fraction=0.95, unmet_eps_kwh=0.0)

    class _FakePio:
        @staticmethod
        def to_html(
            fig, full_html: bool = False, include_plotlyjs: str | bool = False
        ) -> str:
            return "<div>fake-chart</div>"

    monkeypatch.setattr(report_mod, "_plotly_imports", lambda: (None, _FakePio(), None))
    monkeypatch.setattr(
        report_mod, "build_plotly_figures", lambda _s, _st, **_kwargs: []
    )

    out = tmp_path / "report.html"
    report_mod.write_plotly_html_report([series], out, settings)
    html = out.read_text()

    assert "Inputs-based HTC" in html
    assert "Outputs-based HTC - unfiltered" in html
    assert "Outputs-based HTC - filtered" in html
    assert "Outputs-based HTC - division method" in html
    assert "Outputs-based HTC - regression method" in html
    assert "Static HTC" not in html
    assert "Dynamic HTC" not in html
    assert "Empirical HTC" not in html
    assert "Calibration HTC" not in html


def test_assumptions_surface_material_extra_flags() -> None:
    series = replace(
        _make_series(),
        fixed_electric_heating={
            "zones_replaced": 1,
            "rated_power_kw_per_zone": 20.0,
            "energy_supply_name": "mains elec",
        },
        extra_flags=["--future-homes-standard", "--custom-flag"],
        calibration=CalibrationData(
            htc_target_w_per_k=100.0,
            htc_baseline_w_per_k=90.0,
            htc_calibrated_w_per_k=95.0,
            residual_w_per_k=5.0,
            scalar=1.1,
            s_global=0.5,
            monthly_labels=[],
            monthly_baseline=[],
            monthly_global=[],
            monthly_calibrated=[],
            monthly_mask_reason="",
            masking_heating_eps_w=0.0,
            masking_unmet_eps_kwh=0.0,
            masking_valid_min_fraction=0.95,
            masking_mask_block_hours=24.0,
            delta_scalar=0.2,
            calibration_stat="mean",
            calibration_block="monthly",
            lambda_reg=0.01,
            scalar_bounds=(0.1, 2.5),
            fee_enabled=True,
            fee_status="ok",
        ),
    )
    settings = PlotSettings(valid_min_fraction=0.95, unmet_eps_kwh=0.0)

    html = report_mod._build_assumptions_html(series, settings)

    assert "Heating system treatment" in html
    assert "20 kW direct electric emitters on mains elec" in html
    assert "Additional engine flags" in html
    assert "--custom-flag" in html
    assert "Postprocessing" in html
    assert "--future-homes-standard-FEE" in html


def test_write_report_uses_each_series_values_for_multi_summary(
    monkeypatch, tmp_path: Path
) -> None:
    first = replace(
        _make_series(),
        path=tmp_path / "first_summary.json",
        htc_design=111.1,
        htc_empirical=222.2,
    )
    second = replace(
        _make_series(),
        path=tmp_path / "second_summary.json",
        htc_design=333.3,
        htc_empirical=444.4,
    )
    settings = PlotSettings(valid_min_fraction=0.95, unmet_eps_kwh=0.0)

    class _FakePio:
        @staticmethod
        def to_html(
            fig, full_html: bool = False, include_plotlyjs: str | bool = False
        ) -> str:
            return "<div>fake-chart</div>"

    monkeypatch.setattr(report_mod, "_plotly_imports", lambda: (None, _FakePio(), None))
    monkeypatch.setattr(
        report_mod, "build_plotly_figures", lambda _s, _st, **_kwargs: []
    )

    out = tmp_path / "report.html"
    report_mod.write_plotly_html_report([first, second], out, settings)
    html = out.read_text()

    assert "111.1 W/K" in html
    assert "222.2 W/K" in html
    assert "333.3 W/K" in html
    assert "444.4 W/K" in html


def test_headline_chart_pairs_baseline_and_calibrated_bars(
    monkeypatch, tmp_path: Path
) -> None:
    n = 400
    settings = PlotSettings(valid_min_fraction=0.95, unmet_eps_kwh=0.0)
    baseline_dir = tmp_path / "baseline_results"
    calibrated_dir = tmp_path / "calibrated_results"

    class _FakeBar:
        def __init__(self, **kwargs) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    class _FakeFigure:
        def __init__(self) -> None:
            self.data = []
            self.layout = SimpleNamespace(
                meta={},
                yaxis=SimpleNamespace(categoryarray=None),
                annotations=[],
            )

        def add_trace(self, trace) -> None:
            self.data.append(trace)

        def add_annotation(self, **kwargs) -> None:
            self.layout.annotations.append(SimpleNamespace(**kwargs))

        def add_vline(self, **kwargs) -> None:
            pass

        def update_layout(self, **kwargs) -> None:
            for key, value in kwargs.items():
                if key == "yaxis" and isinstance(value, dict):
                    for y_key, y_val in value.items():
                        setattr(self.layout.yaxis, y_key, y_val)
                else:
                    setattr(self.layout, key, value)

        def update_xaxes(self, **kwargs) -> None:
            self.layout.xaxis = SimpleNamespace(**kwargs)

        def update_yaxes(self, **kwargs) -> None:
            for key, value in kwargs.items():
                setattr(self.layout.yaxis, key, value)

    fake_go = SimpleNamespace(Figure=_FakeFigure, Bar=_FakeBar)
    monkeypatch.setattr(
        "htc_harness.plotting.figures._plotly_imports",
        lambda: (fake_go, None, None),
    )

    baseline = replace(
        _make_series(n=n),
        path=tmp_path / "summary_baseline.json",
        base_results_dir=baseline_dir,
        q_loss_by_offset={"0.0": [1000.0] * n, "1.0": [850.0] * n},
        q_loss_components_by_offset={
            "0.0": {
                "FabricHeatLoss": [600.0] * n,
                "ThermalBridges": [200.0] * n,
                "InfiltrationVentilation": [200.0] * n,
            },
            "1.0": {
                "FabricHeatLoss": [500.0] * n,
                "ThermalBridges": [180.0] * n,
                "InfiltrationVentilation": [170.0] * n,
            },
        },
    )
    calibrated = replace(
        _make_series(n=n),
        path=tmp_path / "summary.json",
        base_results_dir=calibrated_dir,
        q_loss_by_offset={"0.0": [1100.0] * n, "1.0": [920.0] * n},
        q_loss_components_by_offset={
            "0.0": {
                "FabricHeatLoss": [650.0] * n,
                "ThermalBridges": [220.0] * n,
                "InfiltrationVentilation": [230.0] * n,
            },
            "1.0": {
                "FabricHeatLoss": [530.0] * n,
                "ThermalBridges": [195.0] * n,
                "InfiltrationVentilation": [195.0] * n,
            },
        },
        calibration=CalibrationData(
            htc_target_w_per_k=172.0,
            htc_baseline_w_per_k=150.0,
            htc_calibrated_w_per_k=180.0,
            residual_w_per_k=-8.0,
            scalar=1.1,
            s_global=0.5,
            monthly_labels=[],
            monthly_baseline=[],
            monthly_global=[],
            monthly_calibrated=[],
            monthly_mask_reason="",
            masking_heating_eps_w=0.0,
            masking_unmet_eps_kwh=0.0,
            masking_valid_min_fraction=0.95,
            masking_mask_block_hours=24.0,
            delta_scalar=0.2,
            calibration_stat="mean",
            calibration_block="weekly",
            lambda_reg=0.01,
            scalar_bounds=(0.1, 2.5),
            htc_static_baseline_w_per_k=160.0,
            htc_static_calibrated_w_per_k=175.0,
            htc_empirical_baseline_w_per_k=150.0,
            htc_empirical_calibrated_w_per_k=180.0,
        ),
    )

    baseline_dir.mkdir()
    calibrated_dir.mkdir()
    _write_static_csv(
        baseline_dir / "results_static.csv",
        fabric=105.0,
        bridges=20.0,
        ventilation=35.0,
        total=160.0,
    )
    _write_static_csv(
        calibrated_dir / "results_static.csv",
        fabric=115.0,
        bridges=22.0,
        ventilation=38.0,
        total=175.0,
    )

    fig = _fig_headline_stacked_bar(calibrated, settings, baseline_series=baseline)

    expected_rows = [
        "Inputs-based HTC<br>Baseline",
        "Inputs-based HTC<br>Calibrated",
        "Outputs-based HTC - unfiltered<br>Baseline",
        "Outputs-based HTC - unfiltered<br>Calibrated",
        "Outputs-based HTC - filtered<br>Baseline",
        "Outputs-based HTC - filtered<br>Calibrated",
    ]
    assert list(fig.layout.yaxis.categoryarray) == list(reversed(expected_rows))

    fabric_trace = next(trace for trace in fig.data if trace.name == "Fabric")
    fabric_by_row = dict(zip(fabric_trace.y, fabric_trace.x))
    assert fabric_by_row["Outputs-based HTC - filtered<br>Baseline"] == 100.0
    assert fabric_by_row["Outputs-based HTC - filtered<br>Calibrated"] == 120.0

    annotation_text = {annotation.text for annotation in fig.layout.annotations}
    assert "  150.0 W/K" in annotation_text
    assert "  180.0 W/K" in annotation_text


def test_load_summary_series_reads_current_engine_metadata(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "job": {
                    "offsets_k": [0.0],
                    "window": {"timestep_hours": 0.5},
                    "engine": {
                        "runner": "rust",
                        "patched_manifest_commit": "abcdef1234567890",
                        "env_overrides": {"HEM_ACH_CAL_FACTOR": "1.25"},
                        "extra_flags": ["--future-homes-standard"],
                    },
                },
                "summary": {
                    "htc_design_w_per_k": 123.4,
                    "htc_empirical_w_per_k": 120.0,
                },
                "derived": {},
                "runs": [],
            }
        )
    )

    series = load_summary_series(summary_path)

    assert series.engine_runner == "rust"
    assert series.engine_commit == "abcdef1"
    assert series.env_overrides == {"HEM_ACH_CAL_FACTOR": "1.25"}
    assert series.extra_flags == ["--future-homes-standard"]
