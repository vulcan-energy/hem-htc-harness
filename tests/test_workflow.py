# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

import json
from pathlib import Path

import pytest

from htc_harness.errors import HarnessError
from htc_harness.workflow import (
    NullProgressReporter,
    resolve_engine_env_overrides,
    run_offsets_batch,
)


def _write_epw(path: Path, rows: int = 4) -> None:
    header = ["header"] * 8
    data = [",".join(["0"] * 6 + ["10.0"] + ["0"] * 20) for _ in range(rows)]
    path.write_text("\n".join(header + data) + "\n")


@pytest.fixture
def model_and_weather(tmp_path: Path) -> tuple[Path, Path]:
    model = tmp_path / "m.json"
    model.write_text(json.dumps({"Zone": {}}))
    weather = tmp_path / "w.epw"
    _write_epw(weather)
    return model, weather


def test_batch_runs_without_subprocess(
    tmp_path: Path, model_and_weather: tuple[Path, Path]
) -> None:
    """The whole batch must be drivable with an injected executor.

    This is what makes the browser demo possible: Pyodide has no subprocess, so
    if anything in the batch path shells out directly, the demo cannot work.
    """
    model, weather = model_and_weather
    commands: list[list[str]] = []

    def executor(command, *, cwd, env):
        commands.append(command)
        # Simulate the engine writing its results directory.
        Path(command[1]).parent.joinpath(f"{Path(command[1]).stem}__results").mkdir(
            exist_ok=True
        )

    results = run_offsets_batch(
        dry_mode=False,
        output_root=tmp_path / "out",
        batch_label="demo",
        resolved_input=model,
        resolved_weather=weather,
        resolved_external_conditions=None,
        offsets_list=[0.0, 1.0, -1.0],
        hem_bin=Path("/opt/hem"),
        detailed_output_heating_cooling=False,
        fhs_assumptions=False,
        extra_flag=(),
        disable_fhs_heating_schedule=False,
        executor=executor,
    )

    assert len(commands) == 3
    assert sorted(results) == [-1.0, 0.0, 1.0]
    # Base run must not be offset; offsets get their own directories.
    assert (tmp_path / "out" / "demo_base").is_dir()
    assert (tmp_path / "out" / "demo_plus1K").is_dir()
    assert (tmp_path / "out" / "demo_minus1K").is_dir()


def test_batch_defaults_to_silent_reporter(
    tmp_path: Path, model_and_weather: tuple[Path, Path], capsys
) -> None:
    """The library prints nothing of its own; the CLI owns all output."""
    model, weather = model_and_weather
    run_offsets_batch(
        dry_mode=False,
        output_root=tmp_path / "out",
        batch_label="quiet",
        resolved_input=model,
        resolved_weather=weather,
        resolved_external_conditions=None,
        offsets_list=[0.0],
        hem_bin=Path("/opt/hem"),
        detailed_output_heating_cooling=False,
        fhs_assumptions=False,
        extra_flag=(),
        disable_fhs_heating_schedule=False,
        executor=lambda command, *, cwd, env: None,
        progress=NullProgressReporter(),
    )
    assert capsys.readouterr().out == ""


def test_dry_mode_requires_existing_results(tmp_path: Path) -> None:
    with pytest.raises(HarnessError, match="Missing results for offset"):
        run_offsets_batch(
            dry_mode=True,
            output_root=tmp_path / "out",
            batch_label="demo",
            resolved_input=tmp_path / "m.json",
            resolved_weather=None,
            resolved_external_conditions=tmp_path / "ec.json",
            offsets_list=[0.0],
            hem_bin=Path("/opt/hem"),
            detailed_output_heating_cooling=False,
            fhs_assumptions=False,
            extra_flag=(),
            disable_fhs_heating_schedule=False,
        )


def test_disable_fhs_heating_schedule_sets_env() -> None:
    assert resolve_engine_env_overrides(
        env_overrides=None, disable_fhs_heating_schedule=True
    ) == {"HEM_FHS_DISABLE_HEATING_SCHEDULE": "1"}
    assert (
        resolve_engine_env_overrides(
            env_overrides=None, disable_fhs_heating_schedule=False
        )
        == {}
    )


def test_preprocess_route_does_not_set_the_env_var() -> None:
    """The two routes are alternatives, not a belt-and-braces pair.

    Setting the variable as well would misreport the run in the engine
    provenance: the summary would claim the patched wrapper disabled the
    schedule when in fact h3 did it in Python.
    """
    assert (
        resolve_engine_env_overrides(
            env_overrides=None,
            disable_fhs_heating_schedule=True,
            fhs_schedule_disable_via="preprocess",
        )
        == {}
    )


def _preprocessed_model() -> dict[str, object]:
    """A model shaped like the wrapper's `<key>__preproc.json`.

    Trimmed to the parts that matter: a heating pattern with FHS's setbacks,
    a hot-water control that must be left alone, and a transparent element
    carrying the one field the core schema rejects.
    """
    setpoint = 20.18
    return {
        "SimulationTime": {"start": 0, "end": 48, "step": 0.5},
        "SpaceHeatSystem": {
            "Zone 1 circuit": {
                "type": "WetDistribution",
                "Control": "HeatingPattern_Zone 1 circuit",
            }
        },
        "Control": {
            "HeatingPattern_Zone 1 circuit": {
                "type": "SetpointTimeControl",
                "start_day": 0,
                "time_series_step": 0.5,
                "advanced_start": 2,
                "schedule": {
                    "main": [{"value": "week", "repeat": 53}],
                    "week": [
                        {"value": "weekday", "repeat": 5},
                        {"value": "weekend", "repeat": 2},
                    ],
                    "weekday": [None] * 14 + [setpoint] * 5 + [None] * 29,
                    "weekend": [None] * 17 + [setpoint] * 10 + [None] * 21,
                },
            },
            "_HW_min_temp": {
                "type": "SetpointTimeControl",
                "schedule": {"main": [52.0, None, 52.0]},
            },
        },
        "Zone": {
            "Zone 1": {
                "BuildingElement": {
                    "Window": {
                        "type": "BuildingElementTransparent",
                        "u_value": 1.2,
                        "areal_heat_capacity": 1000.0,
                    },
                    "Wall": {
                        "type": "BuildingElementOpaque",
                        "u_value": 0.18,
                        "areal_heat_capacity": 110000.0,
                    },
                }
            }
        },
    }


def test_flattening_holds_heating_at_setpoint_and_drops_advanced_start() -> None:
    from htc_harness.reader import flatten_fhs_heating_schedule_to_model

    flattened, info = flatten_fhs_heating_schedule_to_model(_preprocessed_model())

    assert info["controls_flattened"] == 1
    assert info["setpoints_c"] == [20.18]

    control = flattened["Control"]["HeatingPattern_Zone 1 circuit"]
    for day in ("weekday", "weekend"):
        schedule = control["schedule"][day]
        assert len(schedule) == 48
        assert schedule == [20.18] * 48, f"{day} still has setbacks or off periods"

    # `main` and `week` name other entries; flattening them would destroy the
    # schedule's structure rather than its setbacks.
    assert control["schedule"]["main"] == [{"value": "week", "repeat": 53}]
    assert control["schedule"]["week"][0] == {"value": "weekday", "repeat": 5}

    # Nothing to start early for once there is no unoccupied period.
    assert "advanced_start" not in control


def test_flattening_leaves_controls_no_heating_system_references() -> None:
    """Hot-water and window controls share the SetpointTimeControl type.

    Flattening them would change the run in ways that have nothing to do with
    the heating schedule this route exists to remove.
    """
    from htc_harness.reader import flatten_fhs_heating_schedule_to_model

    flattened, _ = flatten_fhs_heating_schedule_to_model(_preprocessed_model())
    assert flattened["Control"]["_HW_min_temp"]["schedule"]["main"] == [
        52.0,
        None,
        52.0,
    ]


def test_core_adaptation_strips_only_transparent_areal_heat_capacity() -> None:
    """One field is the whole gap between the preprocessed model and core.

    `BuildingElementTransparent` sets `additionalProperties: false` in the core
    schema and does not list `areal_heat_capacity`, which the wrapper resolves
    onto every element. Opaque elements legitimately carry it.
    """
    from htc_harness.reader import adapt_preprocessed_model_for_core

    adapted, info = adapt_preprocessed_model_for_core(_preprocessed_model())

    assert info["transparent_areal_heat_capacity_removed"] == 1
    elements = adapted["Zone"]["Zone 1"]["BuildingElement"]
    assert "areal_heat_capacity" not in elements["Window"]
    assert elements["Window"]["u_value"] == 1.2
    assert elements["Wall"]["areal_heat_capacity"] == 110000.0


def test_preprocess_route_runs_the_wrapper_then_the_core_engine(
    tmp_path: Path,
) -> None:
    """Two invocations per offset, and the second must be the core engine.

    If the second run were the wrapper again it would re-apply the standard
    heating pattern over the flattened one, which is exactly what this route
    exists to prevent — and the resulting HTC would look plausible.
    """
    model = tmp_path / "m.json"
    model.write_text(json.dumps({"Zone": {}}))
    epw = tmp_path / "w.epw"
    _write_epw(epw)

    invocations: list[list[str]] = []

    def executor(command: list[str], *, cwd: Path | None, env: dict) -> None:
        invocations.append(list(command))
        if "--preprocess-only" not in command:
            return
        # Stand in for the wrapper: write the model it would have produced.
        results = Path(command[1]).parent / f"{Path(command[1]).stem}__results"
        results.mkdir(parents=True, exist_ok=True)
        (results / "FHS__preproc.json").write_text(json.dumps(_preprocessed_model()))

    results_dirs = run_offsets_batch(
        dry_mode=False,
        output_root=tmp_path / "out",
        batch_label="demo",
        resolved_input=model,
        resolved_weather=epw,
        resolved_external_conditions=None,
        offsets_list=[0.0],
        hem_bin=Path("/opt/hem"),
        fhs_bin=Path("/opt/fhs"),
        detailed_output_heating_cooling=False,
        fhs_assumptions=True,
        extra_flag=(),
        disable_fhs_heating_schedule=True,
        fhs_schedule_disable_via="preprocess",
        executor=executor,
        progress=NullProgressReporter(),
    )

    assert len(invocations) == 2
    wrapper, core = invocations

    assert wrapper[0] == "/opt/hem" or wrapper[0] == "/opt/fhs"
    assert "--mode" in wrapper and "--preprocess-only" in wrapper

    assert "--mode" not in core, "the second run must be the core engine"
    assert "--preprocess-only" not in core
    assert core[0] == "/opt/hem"
    # The wrapper baked the offset external conditions into the preprocessed
    # model. Passing the EPW again would apply the offset a second time.
    assert "-e" not in core

    core_input = Path(core[1])
    assert core_input.name.endswith("__preproc_flat.json")
    ran = json.loads(core_input.read_text())
    control = ran["Control"]["HeatingPattern_Zone 1 circuit"]
    assert control["schedule"]["weekday"] == [20.18] * 48
    assert (
        "areal_heat_capacity"
        not in (ran["Zone"]["Zone 1"]["BuildingElement"]["Window"])
    )

    # The analysis must read the core run's output, not the wrapper's.
    assert results_dirs[0.0].name == f"{core_input.stem}__results"


def test_preprocess_route_shortens_the_run_on_the_core_input(
    tmp_path: Path,
) -> None:
    """The period must land on the model the core engine runs, and only there.

    The wrapper writes its own `SimulationTime` while resolving the weather year
    and the schedules against it, so this is the first point at which an
    override survives. Applying it to the wrapper's input instead would either
    be overwritten or change what the wrapper derives.
    """
    model = tmp_path / "m.json"
    model.write_text(json.dumps({"Zone": {}}))
    epw = tmp_path / "w.epw"
    _write_epw(epw)

    invocations: list[list[str]] = []

    def executor(command: list[str], *, cwd: Path | None, env: dict) -> None:
        invocations.append(list(command))
        if "--preprocess-only" not in command:
            return
        results = Path(command[1]).parent / f"{Path(command[1]).stem}__results"
        results.mkdir(parents=True, exist_ok=True)
        preprocessed = _preprocessed_model()
        # What the wrapper writes: the whole year.
        preprocessed["SimulationTime"] = {"start": 0.0, "end": 8760.0, "step": 0.5}
        (results / "FHS__preproc.json").write_text(json.dumps(preprocessed))

    run_offsets_batch(
        dry_mode=False,
        output_root=tmp_path / "out",
        batch_label="demo",
        resolved_input=model,
        resolved_weather=epw,
        resolved_external_conditions=None,
        offsets_list=[0.0],
        hem_bin=Path("/opt/hem"),
        fhs_bin=Path("/opt/fhs"),
        detailed_output_heating_cooling=False,
        fhs_assumptions=True,
        extra_flag=(),
        disable_fhs_heating_schedule=True,
        fhs_schedule_disable_via="preprocess",
        simulation_period=(0.0, 2160.0, 0.5),
        executor=executor,
        progress=NullProgressReporter(),
    )

    wrapper, core = invocations
    # The wrapper saw the authored model, which carries no period at all.
    assert "SimulationTime" not in json.loads(Path(wrapper[1]).read_text())

    ran = json.loads(Path(core[1]).read_text())
    assert ran["SimulationTime"] == {"start": 0.0, "end": 2160.0, "step": 0.5}
    # Shortening must not have disturbed the flattening that runs beside it.
    assert (
        ran["Control"]["HeatingPattern_Zone 1 circuit"]["schedule"]["weekday"]
        == [20.18] * 48
    )


def test_preprocess_route_leaves_the_period_alone_by_default(tmp_path: Path) -> None:
    """No period means the wrapper's own, so `h3 run` is unaffected by any of this."""
    model = tmp_path / "m.json"
    model.write_text(json.dumps({"Zone": {}}))
    epw = tmp_path / "w.epw"
    _write_epw(epw)

    invocations: list[list[str]] = []

    def executor(command: list[str], *, cwd: Path | None, env: dict) -> None:
        invocations.append(list(command))
        if "--preprocess-only" not in command:
            return
        results = Path(command[1]).parent / f"{Path(command[1]).stem}__results"
        results.mkdir(parents=True, exist_ok=True)
        preprocessed = _preprocessed_model()
        preprocessed["SimulationTime"] = {"start": 0.0, "end": 8760.0, "step": 0.5}
        (results / "FHS__preproc.json").write_text(json.dumps(preprocessed))

    run_offsets_batch(
        dry_mode=False,
        output_root=tmp_path / "out",
        batch_label="demo",
        resolved_input=model,
        resolved_weather=epw,
        resolved_external_conditions=None,
        offsets_list=[0.0],
        hem_bin=Path("/opt/hem"),
        fhs_bin=Path("/opt/fhs"),
        detailed_output_heating_cooling=False,
        fhs_assumptions=True,
        extra_flag=(),
        disable_fhs_heating_schedule=True,
        fhs_schedule_disable_via="preprocess",
        executor=executor,
        progress=NullProgressReporter(),
    )

    ran = json.loads(Path(invocations[1][1]).read_text())
    assert ran["SimulationTime"] == {"start": 0.0, "end": 8760.0, "step": 0.5}


def test_preprocess_route_reports_a_missing_preprocessed_model(
    tmp_path: Path,
) -> None:
    """Silence here would mean running with the standard schedule unnoticed."""
    model = tmp_path / "m.json"
    model.write_text(json.dumps({"Zone": {}}))
    epw = tmp_path / "w.epw"
    _write_epw(epw)

    with pytest.raises(HarnessError, match="wrote no model"):
        run_offsets_batch(
            dry_mode=False,
            output_root=tmp_path / "out",
            batch_label="demo",
            resolved_input=model,
            resolved_weather=epw,
            resolved_external_conditions=None,
            offsets_list=[0.0],
            hem_bin=Path("/opt/hem"),
            fhs_bin=Path("/opt/fhs"),
            detailed_output_heating_cooling=False,
            fhs_assumptions=True,
            extra_flag=(),
            disable_fhs_heating_schedule=True,
            fhs_schedule_disable_via="preprocess",
            executor=lambda command, *, cwd, env: None,
            progress=NullProgressReporter(),
        )
