# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

import ast
import json
import subprocess
from dataclasses import fields
from pathlib import Path

import pytest

from htc_harness.api import HtcRunOptions, resolve_offsets, run_htc_analysis
from htc_harness.config import HarnessConfig

# The layer the browser demo imports. Pyodide provides no working subprocess and
# the site does not ship click, so none of these may depend on either.
LIBRARY_MODULES = (
    "api.py",
    "workflow.py",
    "paths.py",
    "errors.py",
    "provenance.py",
    "empirical.py",
    "calibration.py",
    "reader.py",
)


def test_library_layer_imports_no_click_or_subprocess() -> None:
    package = Path(__file__).resolve().parent.parent / "src" / "htc_harness"
    offenders: list[str] = []
    for name in LIBRARY_MODULES:
        tree = ast.parse((package / name).read_text())
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if module.split(".")[0] in {"click", "subprocess"}:
                    offenders.append(f"{name} imports {module}")
    assert not offenders, "library layer must stay UI- and subprocess-free: " + str(
        offenders
    )


def test_browser_api_exposes_only_the_fixed_sandbox() -> None:
    """The public browser surface must not accept arbitrary uploaded models."""
    from htc_harness import browser

    assert not hasattr(browser, "write_input_files")
    assert not hasattr(browser, "run_demo")


def test_resolve_offsets_puts_base_first() -> None:
    assert resolve_offsets((), include_base=True) == [0.0, -1.0, 1.0]
    assert resolve_offsets((), include_base=False) == [-1.0, 1.0]
    assert resolve_offsets((2.0,), include_base=True) == [0.0, 2.0]
    # An explicit base offset is not duplicated.
    assert resolve_offsets((0.0, 2.0), include_base=True) == [0.0, 2.0]


def test_run_pipeline_never_shells_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole run pipeline must be drivable without a subprocess.

    This is the property the in-browser demo depends on. Any direct shell-out
    added to the run path will fail here rather than at the Pyodide boundary.

    Runs at the stock HtcRunOptions defaults, so it also covers the FHS wrapper
    and fixed-electric-heating paths the CLI takes by default.
    """
    # The layout `h3 build-hem` produces; the FHS wrapper is looked up relative
    # to the engine binary.
    workdir = tmp_path / ".patch-workdir"
    hem_bin = workdir / "rust" / "target" / "release" / "hem"
    hem_bin.parent.mkdir(parents=True)
    hem_bin.touch()
    fhs_bin = workdir / "fhs" / "target" / "release" / "future-homes-standard"
    fhs_bin.parent.mkdir(parents=True)
    fhs_bin.touch()

    model = tmp_path / "m.json"
    model.write_text(json.dumps({"Zone": {}}))
    weather = tmp_path / "w.epw"
    rows = ["hdr"] * 8 + [",".join(["0"] * 6 + ["10.0"] + ["0"] * 20) for _ in range(4)]
    weather.write_text("\n".join(rows) + "\n")

    def explode(*args, **kwargs):
        raise AssertionError("the run pipeline must not launch a subprocess")

    monkeypatch.setattr(subprocess, "run", explode)
    monkeypatch.setattr(subprocess, "Popen", explode)

    invocations: list[list[str]] = []

    def executor(command, *, cwd, env):
        invocations.append(command)
        results = Path(command[1]).parent / f"{Path(command[1]).stem}__results"
        results.mkdir(exist_ok=True)

    # analyze_offsets needs real HEM CSVs, which a stub executor cannot produce.
    # Reaching it at all proves every step before it stayed subprocess-free.
    with pytest.raises((FileNotFoundError, OSError, ValueError, KeyError)):
        run_htc_analysis(
            options=HtcRunOptions(
                input_path=model,
                weather_path=weather,
                hem_bin=hem_bin,
                out_dir=tmp_path / "out",
            ),
            config=HarnessConfig(),
            executor=executor,
            write_summary=False,
        )

    # base plus the two default offsets
    assert len(invocations) == 3
    # The default is the FHS wrapper, not the core engine.
    assert invocations[0][0] == str(fhs_bin)
    assert "--mode" in invocations[0]


def test_relative_config_dirs_are_not_resolved_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative configured base dir must not be prepended twice.

    run_cli resolves paths before calling the API (weather normalisation needs
    the resolved EC path). Without paths_already_resolved, a relative
    input_dir would turn `inputs/x.json` into `inputs/inputs/x.json`.
    """
    from htc_harness.paths import resolve_and_validate_inputs

    monkeypatch.chdir(tmp_path)
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "x.json").write_text("{}")
    (tmp_path / "inputs" / "w.epw").write_text("x")

    config = HarnessConfig(input_dir=Path("inputs"), weather_dir=Path("inputs"))

    once, weather, _ = resolve_and_validate_inputs(
        config=config,
        input_path=Path("x.json"),
        weather_path=Path("w.epw"),
        external_conditions_path=None,
        dry_mode=False,
    )
    assert once == Path("inputs/x.json")

    twice, _, _ = resolve_and_validate_inputs(
        config=config,
        input_path=once,
        weather_path=weather,
        external_conditions_path=None,
        dry_mode=False,
        already_resolved=True,
    )
    assert twice == once, "already-resolved paths must pass through unchanged"


def test_weather_normalisation_writes_to_output_and_notes(tmp_path: Path) -> None:
    """Normalisation lives in the library now, not the CLI.

    Moving it here is what removed the paths_already_resolved workaround: the
    CLI no longer has to pre-resolve paths just to normalise, so resolution
    happens exactly once.
    """
    from htc_harness.api import apply_weather_normalisation

    ec = tmp_path / "ec.json"
    ec.write_text(
        json.dumps(
            {
                "ExternalConditions": {
                    "air_temperatures": [10.0] * 8760,
                    "wind_speeds": [3.0] * 8760,
                    "diffuse_horizontal_radiation": [100.0] * 8760,
                    "direct_beam_radiation": [200.0] * 8760,
                }
            }
        )
    )
    out_root = tmp_path / "out"
    path, labels = apply_weather_normalisation(
        options=HtcRunOptions(
            input_path=tmp_path / "m.json", flatten_solar=True, remove_wind=True
        ),
        external_conditions_path=ec,
        output_root=out_root,
    )

    assert labels == ["flatten-solar", "remove-wind"]
    # Written under the output root, never beside the original input.
    assert path.parent == out_root
    assert path.name == "ec__flatten-solar_remove-wind.json"

    written = json.loads(path.read_text())["ExternalConditions"]
    assert set(written["wind_speeds"]) == {0.0}
    assert len(set(written["diffuse_horizontal_radiation"])) == 1


def test_normalisation_rejects_epw_weather(tmp_path: Path) -> None:
    from htc_harness.errors import HarnessUsageError

    with pytest.raises(HarnessUsageError, match="requires --external-conditions"):
        run_htc_analysis(
            options=HtcRunOptions(
                input_path=tmp_path / "m.json",
                weather_path=tmp_path / "w.epw",
                flatten_solar=True,
            ),
            config=HarnessConfig(),
        )


def test_sandbox_model_ships_inside_the_wheel() -> None:
    """The host page fetches one artifact, not a wheel plus a loose model.

    If package-data ever stops including it, an installed wheel raises here
    rather than the browser sandbox failing to start with no obvious cause.
    """
    from htc_harness.sandbox import load_sandbox_model

    model = load_sandbox_model()
    assert model["GroundFloorArea"] > 0
    assert model["Zone"]["Zone 1"]["BuildingElement"], "model has no fabric"
    # Every attribute the sandbox exposes needs somewhere to land.
    assert isinstance(model["Zone"]["Zone 1"]["ThermalBridging"], (int, float))
    assert model["InfiltrationVentilation"]["Leaks"]["test_result"] > 0


def test_sandbox_model_is_returned_fresh_each_call() -> None:
    """Callers mutate it through the lever transforms; a shared dict would leak."""
    from htc_harness.sandbox import load_sandbox_model

    first = load_sandbox_model()
    first["GroundFloorArea"] = -1
    assert load_sandbox_model()["GroundFloorArea"] > 0


def test_sandbox_weather_ships_inside_the_wheel() -> None:
    """The model carries no weather of its own, so the EPW has to travel with it.

    A full year, because the FHS wrapper preprocesses against one; the run is
    shortened afterwards, not by trimming this file.
    """
    from htc_harness.sandbox import load_sandbox_weather

    weather = load_sandbox_weather()
    assert weather.startswith("LOCATION,")
    # 8 header lines plus 8760 hourly records.
    assert len(weather.strip().splitlines()) == 8768


def test_simulation_period_requires_the_preprocess_route(tmp_path: Path) -> None:
    """A period that could not be applied must fail, not be quietly dropped.

    It is written onto the wrapper's preprocessed model, so on any other route
    there is nowhere to put it — and a run that silently covered a whole year
    when three months were asked for is wrong in a way nothing reports.
    """
    from htc_harness.errors import HarnessUsageError

    with pytest.raises(HarnessUsageError, match="preprocess route"):
        run_htc_analysis(
            options=HtcRunOptions(
                input_path=tmp_path / "model.json",
                weather_path=tmp_path / "weather.epw",
                simulation_period=(0.0, 2160.0, 0.5),
                fhs_schedule_disable_via="env",
            ),
            config=HarnessConfig(),
            executor=lambda *args, **kwargs: None,
            write_summary=False,
        )


def test_sandbox_runs_match_the_cli_except_for_the_period(tmp_path: Path) -> None:
    """The sandbox may shorten the run. It may not quietly change anything else.

    Same guarantee as `test_browser_demo_only_overrides_what_wasm_forces`, for
    the sliders rather than an uploaded model: whatever the sandbox reports has
    to be what `h3 run` would report for the same dwelling over the same period.
    """
    from htc_harness import browser

    options = browser.sandbox_options(
        model_path=tmp_path / "model.json",
        weather_path=tmp_path / "weather.epw",
        out_dir=tmp_path / "out",
        label="design",
        offsets=(0.0,),
        hem_bin="/hem/hem",
    )
    stock = HtcRunOptions(input_path=options.input_path)
    differing = {
        field.name
        for field in fields(HtcRunOptions)
        if getattr(options, field.name) != getattr(stock, field.name)
    }
    plumbing = {"weather_path", "out_dir", "label", "hem_bin", "fhs_bin", "offsets"}

    assert differing - plumbing == {
        "require_patched",
        "fhs_schedule_disable_via",
        # The one deliberate difference: Jan-Mar rather than the whole year.
        "simulation_period",
    }
    assert options.simulation_period == browser.SANDBOX_PERIOD_HOURS
    assert options.fixed_electric_heating is True
    assert options.fhs_assumptions is True


def test_sandbox_design_and_empirical_run_the_same_analysis(tmp_path: Path) -> None:
    """Both figures appear together, so they must come from the same options.

    Only the offsets differ: design reads a fabric figure off one run, the
    empirical estimate regresses across three.
    """
    from htc_harness import browser

    shared = {
        "model_path": tmp_path / "model.json",
        "weather_path": tmp_path / "weather.epw",
        "hem_bin": "/hem/hem",
    }
    design = browser.sandbox_options(
        out_dir=tmp_path / "d", label="design", offsets=(0.0,), **shared
    )
    empirical = browser.sandbox_options(
        out_dir=tmp_path / "e", label="empirical", offsets=(), **shared
    )

    differing = {
        field.name
        for field in fields(HtcRunOptions)
        if getattr(design, field.name) != getattr(empirical, field.name)
    }
    assert differing == {"out_dir", "label", "offsets"}


def test_sandbox_rejects_an_unknown_attribute() -> None:
    """A renamed slider still sending its old key must not silently do nothing."""
    from htc_harness import browser
    from htc_harness.errors import HarnessError

    assert browser.levers_from_attributes({"wallU": 0.5}).wall_u_value == 0.5
    with pytest.raises(HarnessError, match="wall_u"):
        browser.levers_from_attributes({"wall_u": 0.5})


def test_fixed_electric_goes_after_preprocessing_on_the_preprocess_route(
    tmp_path: Path,
) -> None:
    """Which slot the substitution lands in decides whether the run works.

    Before the wrapper it is validated by the FHS input schema, which rejects
    the `frac_convective` the core schema requires. On the preprocess route the
    core engine is the one that validates, so the substitution has to be written
    onto the wrapper's output instead.
    """
    from htc_harness import api

    preprocess = HtcRunOptions(
        input_path=tmp_path / "m.json",
        fhs_schedule_disable_via="preprocess",
    )
    transform = api.model_transform_for(preprocess)
    assert transform is not None and transform.after_preprocess is True

    # The default route hands the model straight to the engine, so it keeps the
    # pre-run substitution.
    env_route = HtcRunOptions(input_path=tmp_path / "m.json")
    env_transform = api.model_transform_for(env_route)
    assert env_transform is not None and env_transform.after_preprocess is False

    assert (
        api.model_transform_for(
            HtcRunOptions(input_path=tmp_path / "m.json", fixed_electric_heating=False)
        )
        is None
    )


def test_sandbox_floor_area_control_is_total_floor_area(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The slider is total floor area, not the footprint the lever scales from.

    The sandbox dwelling has two storeys: 35.6 m2 of `GroundFloorArea` and
    71.2 m2 of total floor area. Passing the page's 71.2 straight through as the
    lever would compute a scale of 71.2/35.6 and silently double the house at
    the control's own default position.
    """
    from htc_harness import browser

    monkeypatch.setattr(browser, "WORK_ROOT", tmp_path)
    authored = browser.load_sandbox_model()
    assert browser.total_floor_area(authored) == pytest.approx(71.2)
    assert authored["GroundFloorArea"] == pytest.approx(35.6)

    # As authored: the dwelling must come back unchanged.
    unchanged = browser._stage_sandbox_run({"floorArea": 71.2}, "t1")
    assert unchanged["floor_area_m2"] == pytest.approx(71.2)
    assert json.loads(unchanged["model_path"].read_text())[
        "GroundFloorArea"
    ] == pytest.approx(35.6)

    # Doubled: the footprint doubles with it, and HLP is quoted against 142.4.
    doubled = browser._stage_sandbox_run({"floorArea": 142.4}, "t2")
    assert doubled["floor_area_m2"] == pytest.approx(142.4)
    assert json.loads(doubled["model_path"].read_text())[
        "GroundFloorArea"
    ] == pytest.approx(71.2)

    # Untouched: the authored total floor area is what HLP divides by.
    default = browser._stage_sandbox_run({"wallU": 0.3}, "t3")
    assert default["floor_area_m2"] == pytest.approx(71.2)
