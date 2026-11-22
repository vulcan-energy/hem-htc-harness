# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import fields
from pathlib import Path

import pytest
from click.testing import CliRunner

from htc_harness import cli as cli_mod
from htc_harness.build_tools import BuildLayout
from htc_harness.cli import main


@pytest.fixture(autouse=True)
def _bypass_venv_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("htc_harness.cli._warn_if_not_repo_venv", lambda: None)


def _run_dir() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "fixtures"
        / "full_runs"
        / "cli_job_scenario_1_0"
    )


def test_run_help_exposes_one_supported_engine_path() -> None:
    result = CliRunner().invoke(main, ["run", "--help"])

    assert result.exit_code == 0
    assert "--runner" not in result.output
    assert "--hem-cli" not in result.output


def test_patch_manifest_contains_only_public_build_inputs() -> None:
    manifest_path = Path(__file__).resolve().parents[1] / "patches" / "manifest.json"

    assert set(json.loads(manifest_path.read_text())) == {"rust", "fhs"}


def test_wheel_includes_every_operative_licence_document() -> None:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(pyproject_path.read_text())["project"]

    assert {
        "LICENSE",
        "NOTICE",
        "ADDITIONAL_TERMS.md",
        "ATTRIBUTION.md",
        "TRADEMARKS.md",
        "LICENSES/*",
    } <= set(project["license-files"])


def test_version_reports_public_origin() -> None:
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert "h3, version 0.2.0" in result.output
    assert "Part of Vulcan Community" in result.output


def test_build_hem_uses_packaged_resources_and_writable_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource_root = tmp_path / "installed/share/htc-harness"
    work_root = tmp_path / "cache/htc-harness"
    rust_bin = work_root / ".patch-workdir/rust/target/release/hem"
    fhs_bin = work_root / ".patch-workdir/fhs/target/release/future-homes-standard"
    calls: list[tuple[str, Path, Path]] = []

    monkeypatch.setattr(
        cli_mod,
        "resolve_build_layout",
        lambda: BuildLayout(resource_root=resource_root, work_root=work_root),
    )

    def fake_rust(
        received_resource_root: Path,
        *,
        work_root: Path,
        clean: bool,
    ) -> Path:
        calls.append(("rust", received_resource_root, work_root))
        assert clean is False
        return rust_bin

    def fake_fhs(
        received_resource_root: Path,
        *,
        engine_dir: Path,
        work_root: Path,
        clean: bool,
    ) -> Path:
        calls.append(("fhs", received_resource_root, work_root))
        assert engine_dir == rust_bin.parent.parent.parent
        assert clean is False
        return fhs_bin

    monkeypatch.setattr(cli_mod, "build_patched_rust_hem", fake_rust)
    monkeypatch.setattr(cli_mod, "build_patched_fhs_wrapper", fake_fhs)

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "config.json"),
            "build-hem",
            "--no-write-config",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        ("rust", resource_root, work_root),
        ("fhs", resource_root, work_root),
    ]
    assert f"Binary: {rust_bin}" in result.output
    assert f"FHS binary: {fhs_bin}" in result.output


def test_parse_window_invalid() -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "run",
            "--dry",
            "--input",
            "x.json",
            "--weather",
            "y.epw",
            "--window",
            "bad",
        ],
    )
    assert result.exit_code != 0


def test_run_dry_uses_config_defaults_and_expected_layout(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    output_base = tmp_path / "output"
    output_base.mkdir()
    config_path.write_text(
        json.dumps(
            {
                "hem_bin": "/opt/hem",
                "output_dir": str(output_base),
            }
        )
    )
    runner = CliRunner()
    # Create a fake existing run layout under output/<input_stem>/...
    # run --dry uses default offsets (-1.0, -0.5, +0.5, +1.0) and expects:
    #   <output_root>/<input_stem>/<label>_{base|plus0.5K|minus0.5K|plus1.0K|minus1.0K}/<input_stem>__results/
    input_stem = "model"
    output_root = output_base / input_stem

    def make_results_dir(run_label: str, q_loss: list[float]) -> Path:
        results_dir = output_root / run_label / f"{input_stem}__results"
        results_dir.mkdir(parents=True)
        _write_minimal_results_csv(
            results_dir / f"{input_stem}__results.csv", n=len(q_loss)
        )
        _write_minimal_static_csv(
            results_dir / f"{input_stem}__results_static.csv", htc=200.0
        )
        _write_minimal_air_node(
            results_dir / f"{input_stem}__results_heat_balance_air_node.csv", q_loss
        )
        _write_minimal_external_boundary(
            results_dir / f"{input_stem}__results_heat_balance_external_boundary.csv",
            q_loss,
        )
        return results_dir

    # Perfectly linear q_loss across offsets with H=100 W/K.
    h_true = 100.0
    a = [1000.0, 1010.0, 1020.0, 1030.0]
    base_vals = a
    plus_vals = [ai - h_true * 0.5 for ai in a]
    minus_vals = [ai - h_true * (-0.5) for ai in a]
    plus1_vals = [ai - h_true * 1.0 for ai in a]
    minus1_vals = [ai - h_true * (-1.0) for ai in a]

    make_results_dir("run_base", base_vals)
    make_results_dir("run_plus0.5K", plus_vals)
    make_results_dir("run_minus0.5K", minus_vals)
    make_results_dir("run_plus1K", plus1_vals)
    make_results_dir("run_minus1K", minus1_vals)

    result = runner.invoke(
        main,
        [
            "--config",
            str(config_path),
            "run",
            "--dry",
            "--no-require-patched",
            "--input",
            f"{input_stem}.json",
            "--weather",
            "weather.epw",
            "--out",
            str(tmp_path / "htc_summary.json"),
        ],
    )
    assert result.exit_code == 0
    out_path = tmp_path / "htc_summary.json"
    assert out_path.exists()
    payload = json.loads(out_path.read_text())
    assert abs(payload["summary"]["htc_empirical_w_per_k"] - h_true) < 1e-9
    assert "q_loss_w_by_offset_k" in payload["derived"]
    assert "htc_w_per_k_by_bin_hours" in payload["derived"]


def _write_minimal_results_csv(path: Path, n: int) -> None:
    lines = ["x", "unit"] + ["0" for _ in range(n)]
    path.write_text("\n".join(lines) + "\n")


def _write_minimal_static_csv(path: Path, htc: float) -> None:
    path.write_text(f"Heat transfer coefficient,W / K,{htc}\n")


def _write_minimal_air_node(path: Path, values_w: list[float]) -> None:
    # Values are written as negative in HEM for losses; harness negates to get +W loss.
    rows = [
        [
            "Timestep",
            "Z:ThermalBridges",
            "Z:InfiltrationVentilation",
            "Z:FabricHeatLoss",
        ],
        ["-", "W", "W", "W"],
    ]
    for i, q_loss_w in enumerate(values_w):
        rows.append([str(i), "0", "0", str(-q_loss_w)])
    path.write_text("\n".join(",".join(r) for r in rows) + "\n")


def _write_minimal_external_boundary(path: Path, values_w: list[float]) -> None:
    # Values are written as negative in HEM for losses; harness negates to get +W loss.
    rows = [
        [
            "Timestep",
            "Z:OpaqueFabricExt",
            "Z:TransparentFabricExt",
            "Z:GroundFabricExt",
        ],
        ["-", "W", "W", "W"],
    ]
    for i, q_loss_w in enumerate(values_w):
        rows.append([str(i), str(-q_loss_w), "0", "0"])
    path.write_text("\n".join(",".join(r) for r in rows) + "\n")


def test_run_dry_uses_measured_htc_for_ratio_and_window(tmp_path: Path) -> None:
    # Provide a measured HTC payload with an index window that selects only the first 2 timesteps.
    config_path = tmp_path / "config.json"
    output_base = tmp_path / "output"
    output_base.mkdir()
    config_path.write_text(
        json.dumps(
            {
                "hem_bin": "/opt/hem",
                "output_dir": str(output_base),
            }
        )
    )
    input_stem = "model"
    output_root = output_base / input_stem

    h_true = 100.0
    a = [1000.0, 1010.0, 5000.0, 6000.0]
    base_vals = a
    plus_vals = [ai - h_true * 0.5 for ai in a]
    minus_vals = [ai - h_true * (-0.5) for ai in a]
    plus1_vals = [ai - h_true * 1.0 for ai in a]
    minus1_vals = [ai - h_true * (-1.0) for ai in a]

    def make_results_dir(run_label: str, q_loss: list[float]) -> None:
        results_dir = output_root / run_label / f"{input_stem}__results"
        results_dir.mkdir(parents=True)
        _write_minimal_results_csv(
            results_dir / f"{input_stem}__results.csv", n=len(q_loss)
        )
        _write_minimal_static_csv(
            results_dir / f"{input_stem}__results_static.csv", htc=200.0
        )
        _write_minimal_air_node(
            results_dir / f"{input_stem}__results_heat_balance_air_node.csv", q_loss
        )
        _write_minimal_external_boundary(
            results_dir / f"{input_stem}__results_heat_balance_external_boundary.csv",
            q_loss,
        )

    make_results_dir("run_base", base_vals)
    make_results_dir("run_plus0.5K", plus_vals)
    make_results_dir("run_minus0.5K", minus_vals)
    make_results_dir("run_plus1K", plus1_vals)
    make_results_dir("run_minus1K", minus1_vals)

    measured_path = tmp_path / "measured_htc.json"
    measured_payload = {
        "schema_version": "0.1",
        "htc_value_w_per_k": 150.0,
        "window": {
            "type": "index",
            "start_idx": 0,
            "end_idx": 2,
            "timestep_hours": 0.5,
        },
        "method_profile": None,
        "source": "test",
        "notes": "test",
    }
    measured_path.write_text(json.dumps(measured_payload))

    out_path = tmp_path / "htc_summary.json"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--config",
            str(config_path),
            "run",
            "--dry",
            "--no-require-patched",
            "--input",
            f"{input_stem}.json",
            "--weather",
            "weather.epw",
            "--measured-htc",
            str(measured_path),
            "--out",
            str(out_path),
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(out_path.read_text())
    assert payload["job"]["window"]["rows_used"] == 2
    assert abs(payload["summary"]["htc_empirical_w_per_k"] - h_true) < 1e-9
    assert (
        abs(payload["summary"]["calibration_ratio_measured_over_empirical"] - 1.5)
        < 1e-9
    )
    # Hourly aggregation uses 2 timesteps per hour at timestep_hours=0.5, so 2 rows => 1 hour-bin.
    assert len(payload["derived"]["htc_w_per_k_by_bin_hours"]["1"]) == 1


def test_setup_reconfigure_writes_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    runner = CliRunner()
    # Interactive prompts: vulcan/individual, input_dir, weather_dir, ec_dir, output_dir
    result = runner.invoke(
        main,
        ["--config", str(config_path), "setup", "--reconfigure"],
        input="individual\n/tmp/inputs\n/tmp/weather\n\n/tmp/output\n",
    )
    assert result.exit_code == 0
    payload = json.loads(config_path.read_text())
    assert payload["input_dir"] == "/tmp/inputs"
    assert payload["weather_dir"] == "/tmp/weather"
    assert payload["output_dir"] == "/tmp/output"


def _patch_calibrate_harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from htc_harness import cli as cli_mod

    monkeypatch.setattr(cli_mod, "_check_patched_binary", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli_mod,
        "resolve_and_validate_inputs",
        lambda **kwargs: (
            kwargs["input_path"],
            kwargs["weather_path"],
            kwargs["external_conditions_path"],
        ),
    )
    monkeypatch.setattr(
        cli_mod,
        "default_run_output_root",
        lambda *args, **kwargs: tmp_path / "out_root",
    )
    monkeypatch.setattr(
        cli_mod,
        "run_offsets_batch",
        lambda **kwargs: {
            offset: (tmp_path / kwargs["batch_label"] / f"{offset:+.3f}")
            for offset in kwargs["offsets_list"]
        },
    )

    def _fake_analyze_offsets(*, results_dirs_by_offset_k, **kwargs):
        first_dir = next(iter(results_dirs_by_offset_k.values()))
        batch_label = first_dir.parent.name
        return {
            "batch_label": batch_label,
            "measured_htc": {"htc_value_w_per_k": 120.0},
            "job": {"window": {"timestep_hours": 1.0}},
            "derived": {
                "month_bins": [{"start": 0, "end": 1, "label": "M1"}],
                "q_loss_w_by_offset_k": {"0.0": [1000.0], "1.0": [900.0]},
            },
        }

    def _fake_masked_monthly(payload, **kwargs):
        batch_label = str(payload.get("batch_label", ""))
        value = 100.0
        iter_vent = re.search(r"_iter_(\d+)_vent$", batch_label)
        iter_fabric = re.search(r"_iter_(\d+)_fabric$", batch_label)
        iter_global = re.search(r"_iter_(\d+)_global$", batch_label)
        iter_cal = re.search(r"_iter_(\d+)_calibrated$", batch_label)
        if iter_vent:
            value = 110.0 + float(iter_vent.group(1))
        elif iter_fabric:
            value = 105.0 + float(iter_fabric.group(1))
        elif iter_global:
            value = 111.0 + float(iter_global.group(1))
        elif iter_cal:
            value = 112.0 + 4.0 * float(iter_cal.group(1))
        elif batch_label.endswith("_vent"):
            value = 110.0
        elif batch_label.endswith("_fabric"):
            value = 105.0
        elif batch_label.endswith("_global"):
            value = 111.0
        elif batch_label.endswith("_calibrated"):
            value = 112.0
        return {
            "masked_aggregate_w_per_k": value,
            "masked_aggregate_stat": "mean",
            "monthly_htc_w_per_k": [value],
            "labels": ["M1"],
            "mask_reason": "ok",
            "mask_diagnostics": {"n_months_total": 1, "n_months_finite": 1},
        }

    monkeypatch.setattr(cli_mod, "analyze_offsets", _fake_analyze_offsets)
    monkeypatch.setattr(
        cli_mod, "masked_monthly_htc_from_summary", _fake_masked_monthly
    )
    monkeypatch.setattr(
        cli_mod,
        "extract_mask_from_summary",
        lambda *args, **kwargs: ([True], "ok"),
    )
    monkeypatch.setattr(
        cli_mod,
        "apply_fabric_scalar_to_model",
        lambda model, scalar: (model, {"ground_u_value_capped": 0}),
    )
    monkeypatch.setattr(
        cli_mod, "attach_engine_provenance_to_payload", lambda *args, **kwargs: None
    )


def test_calibrate_opt_steps_zero_preserves_default_behaviour(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_calibrate_harness(monkeypatch, tmp_path)
    measured = tmp_path / "measured.json"
    measured.write_text(json.dumps({"htc_value_w_per_k": 120.0}))
    out_path = tmp_path / "summary.json"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "calibrate",
            "--hem-bin",
            str(tmp_path / "hem"),
            "--input",
            str(tmp_path / "input.json"),
            "--weather",
            str(tmp_path / "weather.epw"),
            "--measured-htc",
            str(measured),
            "--opt-steps",
            "0",
            "--no-plot",
            "--out",
            str(out_path),
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(out_path.read_text())
    iterative = payload["calibration"]["iterative"]
    assert isinstance(payload["calibration"]["scalars"]["scalar"], float)
    assert iterative["enabled"] is False
    assert iterative["executed_steps"] == 0
    assert iterative["stopped_reason"] == "disabled"
    assert iterative["history"] == []


def test_calibrate_iterative_mode_writes_history(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_calibrate_harness(monkeypatch, tmp_path)
    measured = tmp_path / "measured.json"
    measured.write_text(json.dumps({"htc_value_w_per_k": 120.0}))
    out_path = tmp_path / "summary_iter.json"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "calibrate",
            "--hem-bin",
            str(tmp_path / "hem"),
            "--input",
            str(tmp_path / "input.json"),
            "--weather",
            str(tmp_path / "weather.epw"),
            "--measured-htc",
            str(measured),
            "--opt-steps",
            "2",
            "--opt-tol-w-per-k",
            "0.5",
            "--no-plot",
            "--out",
            str(out_path),
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(out_path.read_text())
    iterative = payload["calibration"]["iterative"]
    assert isinstance(payload["calibration"]["scalars"]["scalar"], float)
    assert iterative["enabled"] is True
    assert iterative["max_steps"] == 2
    assert iterative["executed_steps"] >= 1
    assert iterative["stopped_reason"] in {"within_tolerance", "zero_increment"}
    assert len(iterative["history"]) == iterative["executed_steps"]
    if iterative["history"]:
        step = iterative["history"][-1]
        assert "step" in step
        assert "scalar_before" in step
        assert "scalar_after" in step
        assert "delta_scalar_applied" in step
        assert "htc_before_w_per_k" in step
        assert "htc_after_w_per_k" in step
        assert "residual_after_w_per_k" in step
        assert "sensitivity_s_global" in step
    assert payload["calibration"]["metrics"]["htc_target_w_per_k"] == 120.0


def test_calibrate_writes_baseline_summary_and_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_calibrate_harness(monkeypatch, tmp_path)
    from htc_harness import cli as cli_mod

    report_calls: list[tuple[Path, Path | None]] = []
    monkeypatch.setattr(
        cli_mod,
        "_maybe_write_report",
        lambda summary_path, report_path=None: report_calls.append(
            (summary_path, report_path)
        ),
    )

    measured = tmp_path / "measured.json"
    measured.write_text(json.dumps({"htc_value_w_per_k": 120.0}))
    out_path = tmp_path / "summary_with_report.json"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "calibrate",
            "--hem-bin",
            str(tmp_path / "hem"),
            "--input",
            str(tmp_path / "input.json"),
            "--weather",
            str(tmp_path / "weather.epw"),
            "--measured-htc",
            str(measured),
            "--opt-steps",
            "0",
            "--out",
            str(out_path),
        ],
    )
    assert result.exit_code == 0

    baseline_path = tmp_path / "summary_with_report_baseline.json"
    assert baseline_path.exists()
    baseline_payload = json.loads(baseline_path.read_text())
    assert baseline_payload["batch_label"].endswith("_base")

    assert report_calls == [
        (baseline_path, tmp_path / "htc_report_BASELINE.html"),
        (out_path, None),
    ]


def test_calibrate_no_plot_skips_reports_but_keeps_baseline_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_calibrate_harness(monkeypatch, tmp_path)
    from htc_harness import cli as cli_mod

    report_calls: list[tuple[Path, Path | None]] = []
    monkeypatch.setattr(
        cli_mod,
        "_maybe_write_report",
        lambda summary_path, report_path=None: report_calls.append(
            (summary_path, report_path)
        ),
    )

    measured = tmp_path / "measured.json"
    measured.write_text(json.dumps({"htc_value_w_per_k": 120.0}))
    out_path = tmp_path / "summary_no_plot.json"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "calibrate",
            "--hem-bin",
            str(tmp_path / "hem"),
            "--input",
            str(tmp_path / "input.json"),
            "--weather",
            str(tmp_path / "weather.epw"),
            "--measured-htc",
            str(measured),
            "--opt-steps",
            "0",
            "--no-plot",
            "--out",
            str(out_path),
        ],
    )
    assert result.exit_code == 0
    assert report_calls == []
    assert (tmp_path / "summary_no_plot_baseline.json").exists()


def test_attach_engine_provenance_includes_env_and_flags() -> None:
    from htc_harness import cli as cli_mod

    payload = {"job": {}}
    cli_mod.attach_engine_provenance_to_payload(
        payload,
        hem_bin=Path("/opt/hem"),
        require_patched=False,
        env_overrides={"HEM_ACH_CAL_FACTOR": "1.25"},
        extra_flags=["--future-homes-standard"],
    )

    assert payload["job"]["engine"] == {
        "runner": "rust",
        "require_patched": False,
        "env_overrides": {"HEM_ACH_CAL_FACTOR": "1.25"},
        "extra_flags": ["--future-homes-standard"],
        "hem_bin": "/opt/hem",
        "patched_manifest_path": None,
    }


def test_bad_window_reports_before_missing_output_dir(tmp_path: Path) -> None:
    """Malformed options must surface before dry-mode's missing-output error.

    Regression guard for the library extraction: reordering validation made a
    bad --window exit 1 (ClickException) instead of 2 (BadParameter).
    """
    # Pin config so the assertion does not depend on the user's machine.
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--config",
            str(config_path),
            "run",
            "--dry",
            "--hem-bin",
            str(tmp_path / "hem"),
            "--no-require-patched",
            "--input",
            "x.json",
            "--weather",
            "y.epw",
            "--out-dir",
            str(tmp_path / "does-not-exist"),
            "--window",
            "bad",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "window must be 'start_idx,end_idx'" in result.output


@pytest.mark.parametrize("command", ["run", "calibrate"])
def test_disable_fhs_heating_schedule_can_be_turned_off(command: str) -> None:
    """The flag needs an off switch to be a choice at all.

    It was declared `is_flag=True, default=True` with no secondary option, so
    HEM_FHS_DISABLE_HEATING_SCHEDULE was set on every run with no way to opt
    out.
    """
    params = {p.name: p for p in main.commands[command].params}
    flag = params["disable_fhs_heating_schedule"]

    assert flag.default is True
    assert "--disable-fhs-heating-schedule" in flag.opts
    assert "--no-disable-fhs-heating-schedule" in flag.secondary_opts


@pytest.mark.parametrize(
    "option_name",
    [
        "fhs_assumptions",
        "disable_fhs_heating_schedule",
        "fixed_electric_heating",
        "fixed_electric_heater_kw_per_zone",
        "timestep_hours",
        "require_patched",
    ],
)
def test_cli_defaults_match_the_library_defaults(option_name: str) -> None:
    """One source of truth for behavioural defaults.

    `run`, `calibrate` and non-CLI callers such as the browser demo must agree
    on what a default run is; restating these in `click` let them drift.
    """
    from htc_harness.api import HtcRunOptions

    library_default = {f.name: f.default for f in fields(HtcRunOptions)}[option_name]
    for command in ("run", "calibrate"):
        params = {p.name: p for p in main.commands[command].params}
        assert params[option_name].default == library_default, command
