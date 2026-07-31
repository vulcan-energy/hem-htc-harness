# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.

"""Python entry points for the fixed, synthetic in-browser sandbox.

The browser deliberately does not accept arbitrary model uploads. It applies a
bounded dwelling configuration and seven numeric attributes to the bundled
synthetic model, then calls HEM via the site's WASM build. Everything here is
plain Python: no `click`, no `subprocess`, and nothing that assumes a real
operating system beyond a writable filesystem.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .api import HtcRunOptions, model_transform_for, run_htc_analysis
from .config import HarnessConfig
from .empirical import compute_design_htc
from .errors import HarnessError
from .reader import (
    ShoeboxLevers,
    apply_shoebox_levers_to_model,
    locate_run_files,
    read_results_static,
)
from .sandbox import load_sandbox_model, load_sandbox_weather
from .workflow import run_offsets_batch

WORK_ROOT = Path("/h3")

# The period the sandbox simulates, as (start_hour, end_hour, step_hours) from
# the start of the year: 1 January to 1 April.
#
# The full year costs 32 s for the three offsets an empirical estimate needs,
# against 8.7 s for this, and buys almost nothing: Jan-Mar carries 47.8% of
# annual heating at the highest temperature difference, while Jun-Aug carries
# 0.2% at a difference h3's masking discards anyway.
#
# Design HTC is unaffected either way — it is a fabric calculation, identical
# from two weeks or a full year.
SANDBOX_PERIOD_HOURS: tuple[float, float, float] = (0.0, 2160.0, 0.5)

# The attribute names the host page sends, mapped to the lever they set. The
# page's own names are camelCase; keeping the mapping here means the contract is
# stated once, in the code that has to honour it.
SANDBOX_LEVER_FIELDS: dict[str, str] = {
    "floorArea": "floor_area_m2",
    "wallU": "wall_u_value",
    "roofU": "roof_u_value",
    "glazingU": "glazing_u_value",
    "glazingArea": "glazing_area_m2",
    "airtightness": "airtightness_q50",
    "thermalBridging": "thermal_bridging_w_per_k",
}

SANDBOX_CONFIGURATION_FIELDS = {
    "dwellingForm",
    "flatFloor",
    "flatAspect",
    "orientation",
}

SANDBOX_DWELLING_FORMS = {"detached", "end-terrace", "mid-terrace", "flat"}
SANDBOX_FLAT_FLOORS = {"ground", "mid", "top"}
SANDBOX_FLAT_ASPECTS = {"single", "dual"}
SANDBOX_ORIENTATIONS = {0, 45, 90, 135, 180, 225, 270, 315}


@dataclass(frozen=True)
class SandboxConfiguration:
    """The discrete choices that select the sandbox dwelling's envelope."""

    dwelling_form: str = "end-terrace"
    flat_floor: str = "mid"
    flat_aspect: str = "single"
    # The authored dwelling's front faces south. The page expresses rotation
    # as the direction the front should face, which is much easier to interpret
    # than a relative angle.
    orientation_degrees: int = 180


@dataclass
class DemoProgress:
    """Forwards progress to the host page instead of a terminal.

    `emit` receives a plain dict so the worker can `postMessage` it directly.
    """

    emit: Callable[[dict[str, Any]], None]

    def note(self, message: str) -> None:
        self.emit({"type": "note", "message": message})

    def batch_start(self, label: str, total: int) -> None:
        self.emit({"type": "batch_start", "label": label, "total": total})

    def run_start(self, label: str, index: int, total: int, offset_k: float) -> None:
        self.emit(
            {
                "type": "run_start",
                "label": label,
                "index": index,
                "total": total,
                "offset_k": offset_k,
            }
        )

    def run_complete(
        self, label: str, index: int, total: int, elapsed_s: float
    ) -> None:
        self.emit(
            {
                "type": "run_complete",
                "label": label,
                "index": index,
                "total": total,
                "elapsed_s": elapsed_s,
            }
        )


def make_wasm_executor(
    run_hem_wasm: Callable[[list[str], str | None, dict[str, str]], None],
) -> Callable[..., None]:
    """Adapt the worker's HEM-WASM callable to the `HemExecutor` protocol.

    `run_hem_wasm` receives the argv the CLI would have used, the working
    directory, and the environment. It is responsible for writing HEM's results
    into the virtual filesystem exactly as the native binary would, because h3
    then reads those files unchanged.
    """

    def executor(
        command: list[str], *, cwd: Path | None, env: Mapping[str, str]
    ) -> None:
        run_hem_wasm(list(command), str(cwd) if cwd else None, dict(env))

    return executor


def levers_from_attributes(values: Mapping[str, Any]) -> ShoeboxLevers:
    """Turn the host page's attribute dict into `ShoeboxLevers`.

    Unknown keys are rejected rather than ignored. A page that renamed a slider
    and kept sending the old key would otherwise show numbers that silently
    stopped responding to it.
    """
    unknown = sorted(
        set(values) - set(SANDBOX_LEVER_FIELDS) - SANDBOX_CONFIGURATION_FIELDS
    )
    if unknown:
        raise HarnessError(
            f"Unknown sandbox attribute(s): {', '.join(unknown)}. "
            f"Expected any of: {', '.join(sorted(SANDBOX_LEVER_FIELDS))}."
        )
    return ShoeboxLevers(
        **{
            SANDBOX_LEVER_FIELDS[key]: float(value)
            for key, value in values.items()
            if key in SANDBOX_LEVER_FIELDS and value is not None
        }
    )


def sandbox_configuration_from_values(
    values: Mapping[str, Any],
) -> SandboxConfiguration:
    """Validate the discrete envelope choices sent by the host page."""

    dwelling_form = values.get("dwellingForm", "end-terrace")
    flat_floor = values.get("flatFloor", "mid")
    flat_aspect = values.get("flatAspect", "single")
    orientation = values.get("orientation", 180)

    if dwelling_form not in SANDBOX_DWELLING_FORMS:
        raise HarnessError(
            f"Unknown dwellingForm {dwelling_form!r}. Expected one of: "
            f"{', '.join(sorted(SANDBOX_DWELLING_FORMS))}."
        )
    if flat_floor not in SANDBOX_FLAT_FLOORS:
        raise HarnessError(
            f"Unknown flatFloor {flat_floor!r}. Expected one of: "
            f"{', '.join(sorted(SANDBOX_FLAT_FLOORS))}."
        )
    if flat_aspect not in SANDBOX_FLAT_ASPECTS:
        raise HarnessError(
            f"Unknown flatAspect {flat_aspect!r}. Expected one of: "
            f"{', '.join(sorted(SANDBOX_FLAT_ASPECTS))}."
        )
    if (
        isinstance(orientation, bool)
        or not isinstance(orientation, (int, float))
        or orientation not in SANDBOX_ORIENTATIONS
    ):
        raise HarnessError(
            f"Unknown orientation {orientation!r}. Expected a 45-degree compass "
            "bearing from 0 to 315."
        )

    return SandboxConfiguration(
        dwelling_form=str(dwelling_form),
        flat_floor=str(flat_floor),
        flat_aspect=str(flat_aspect),
        orientation_degrees=int(orientation),
    )


def _zone_elements(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    zones = model.get("Zone")
    zone = zones.get("Zone 1") if isinstance(zones, dict) else None
    elements = zone.get("BuildingElement") if isinstance(zone, dict) else None
    if not isinstance(elements, dict):
        raise HarnessError("Sandbox model has no Zone 1 building elements.")
    return elements


def _external_wall_from(
    template: Mapping[str, Any],
    *,
    orientation: int,
    area: float,
    width: float,
    base_height: float = 0.0,
) -> dict[str, Any]:
    wall = json.loads(json.dumps(template))
    wall.update(
        {
            "type": "BuildingElementOpaque",
            "orientation360": orientation,
            "pitch": 90,
            "area": area,
            "width": width,
            "height": 2.8,
            "base_height": base_height,
            "is_external_door": False,
            "is_unheated_pitched_roof": False,
        }
    )
    return wall


def _party_wall_from(template: Mapping[str, Any], *, area: float) -> dict[str, Any]:
    wall = json.loads(json.dumps(template))
    wall["area"] = area
    return wall


def _window_for_flat(
    template: Mapping[str, Any], *, orientation: int
) -> dict[str, Any]:
    window = json.loads(json.dumps(template))
    window["orientation360"] = orientation
    window["base_height"] = min(float(window.get("base_height", 1.0)), 1.0)
    height = float(window.get("height", 1.0))
    window["mid_height"] = window["base_height"] + height / 2
    for part in window.get("window_part_list", []):
        if isinstance(part, dict) and "mid_height_air_flow_path" in part:
            part["mid_height_air_flow_path"] = window["mid_height"]
    return window


def _configure_house(model: dict[str, Any], dwelling_form: str) -> None:
    elements = _zone_elements(model)
    if dwelling_form == "end-terrace":
        return

    if dwelling_form == "detached":
        for suffix in ("", " 1"):
            party_name = f"Party Wall (E){suffix}"
            party = elements.pop(party_name)
            template = elements[f"Wall (W){suffix}"]
            elements[f"Wall (E){suffix}"] = _external_wall_from(
                template,
                orientation=90,
                area=float(party["area"]),
                width=8.9,
                base_height=float(template.get("base_height", 0.0)),
            )
        return

    # A mid-terrace has conditioned neighbours on both long sides. The one
    # west-facing window in the authored end-terrace becomes part of the party
    # wall area; leaving it in would put an external opening into another home.
    west_window_area = 0.0
    for name, element in list(elements.items()):
        if (
            element.get("type") == "BuildingElementTransparent"
            and element.get("orientation360") == 270
        ):
            west_window_area += float(element.get("width", 0)) * float(
                element.get("height", 0)
            )
            elements.pop(name)

    for suffix in ("", " 1"):
        wall = elements.pop(f"Wall (W){suffix}")
        template = elements[f"Party Wall (E){suffix}"]
        area = float(wall["area"])
        if suffix:
            area += west_window_area
        elements[f"Party Wall (W){suffix}"] = _party_wall_from(template, area=area)


def _configure_flat(model: dict[str, Any], floor: str, aspect: str) -> None:
    """Replace the house envelope with the synthetic one-storey flat archetype.

    This is an authored topology, not a blind relabelling of the two-storey
    house. Vertical party walls, conditioned floors/ceilings and exposed
    horizontal elements are built explicitly for the selected position.
    """

    elements = _zone_elements(model)
    opaque_template = elements["Wall (W)"]
    party_template = elements["Party Wall (E)"]
    adjacent_template = elements["Internal Ceiling"]
    ground_template = elements["Floor"]
    roof_template = elements["Pitched Roof (S)"]

    front_window_names = ("Window", "Window 2", "Window 2 2")
    front_windows = {
        f"Front window {index + 1}": _window_for_flat(elements[name], orientation=180)
        for index, name in enumerate(front_window_names)
    }
    front_glazing_area = sum(
        float(window["width"]) * float(window["height"])
        for window in front_windows.values()
    )
    front_door = json.loads(json.dumps(elements["Door"]))
    front_door.update({"orientation360": 180, "base_height": 0})

    flat_elements: dict[str, dict[str, Any]] = {
        "Front wall": _external_wall_from(
            opaque_template,
            orientation=180,
            area=22.4 - float(front_door["area"]) - front_glazing_area,
            width=8.0,
        ),
        "Front door": front_door,
        **front_windows,
        "Party wall (rear)": _party_wall_from(party_template, area=22.4),
        "Party wall (east)": _party_wall_from(party_template, area=24.92),
    }

    if aspect == "dual":
        side_window = _window_for_flat(elements["Window 2 1"], orientation=270)
        side_glazing_area = float(side_window["width"]) * float(side_window["height"])
        flat_elements["Side wall"] = _external_wall_from(
            opaque_template,
            orientation=270,
            area=24.92 - side_glazing_area,
            width=8.9,
        )
        flat_elements["Side window"] = side_window
    else:
        flat_elements["Party wall (west)"] = _party_wall_from(
            party_template, area=24.92
        )

    adjacent_floor = json.loads(json.dumps(adjacent_template))
    adjacent_floor.update({"area": 71.2, "pitch": 0})
    adjacent_ceiling = json.loads(json.dumps(adjacent_template))
    adjacent_ceiling.update({"area": 71.2, "pitch": 180})

    if floor == "ground":
        ground = json.loads(json.dumps(ground_template))
        ground.update({"area": 71.2, "total_area": 71.2, "perimeter": 33.8})
        flat_elements["Ground floor"] = ground
        flat_elements["Conditioned ceiling"] = adjacent_ceiling
    elif floor == "mid":
        flat_elements["Conditioned floor"] = adjacent_floor
        flat_elements["Conditioned ceiling"] = adjacent_ceiling
    else:
        roof = json.loads(json.dumps(roof_template))
        roof.update(
            {
                "pitch": 0,
                "base_height": 2.8,
                "height": 8.0,
                "width": 8.9,
                "area": 71.2,
                "is_unheated_pitched_roof": False,
            }
        )
        roof.pop("orientation360", None)
        flat_elements["Conditioned floor"] = adjacent_floor
        flat_elements["Flat roof"] = roof

    zone = model["Zone"]["Zone 1"]
    zone["BuildingElement"] = flat_elements
    zone["volume"] = 199.36
    zone["livingroom_area"] = 12.86
    zone["restofdwelling_area"] = 58.34

    storey = {"ground": 1, "mid": 2, "top": 3}[floor]
    model["General"] = {
        "build_type": "flat",
        "storeys_in_dwelling": 1,
        "storey_of_dwelling": storey,
        "storeys_in_building": 3,
    }
    model["GroundFloorArea"] = 71.2
    model["BuildingLength"] = 8.9
    model["BuildingWidth"] = 8.0
    leaks = model.get("InfiltrationVentilation", {}).get("Leaks", {})
    if isinstance(leaks, dict):
        leaks["ventilation_zone_height"] = 2.8


def _rotate_orientations(value: Any, delta: int) -> None:
    if isinstance(value, dict):
        orientation = value.get("orientation360")
        if isinstance(orientation, (int, float)):
            value["orientation360"] = (float(orientation) + delta) % 360
        for child in value.values():
            _rotate_orientations(child, delta)
    elif isinstance(value, list):
        for child in value:
            _rotate_orientations(child, delta)


def sandbox_envelope_area(model: Mapping[str, Any]) -> float:
    """Area used by the infiltration model for the configured envelope."""

    total = 0.0
    zones = model.get("Zone")
    if not isinstance(zones, dict):
        return total
    included = {
        "BuildingElementOpaque",
        "BuildingElementTransparent",
        "BuildingElementGround",
        "BuildingElementPartyWall",
    }
    for zone in zones.values():
        elements = zone.get("BuildingElement") if isinstance(zone, dict) else None
        if not isinstance(elements, dict):
            continue
        for element in elements.values():
            if not isinstance(element, dict) or element.get("type") not in included:
                continue
            if element.get("type") == "BuildingElementTransparent":
                width = element.get("width")
                height = element.get("height")
                if isinstance(width, (int, float)) and isinstance(height, (int, float)):
                    total += float(width) * float(height)
            elif isinstance(element.get("area"), (int, float)):
                total += float(element["area"])
    return total


def _set_sandbox_envelope_area(model: dict[str, Any]) -> None:
    leaks = model.get("InfiltrationVentilation", {}).get("Leaks", {})
    if not isinstance(leaks, dict):
        raise HarnessError("Sandbox model has no InfiltrationVentilation.Leaks.")
    leaks["env_area"] = sandbox_envelope_area(model)


def sandbox_model_for_configuration(
    configuration: SandboxConfiguration,
) -> dict[str, Any]:
    """Return a fresh, configured synthetic dwelling model."""

    model = load_sandbox_model()
    if configuration.dwelling_form == "flat":
        _configure_flat(model, configuration.flat_floor, configuration.flat_aspect)
    else:
        _configure_house(model, configuration.dwelling_form)

    _rotate_orientations(model, configuration.orientation_degrees - 180)
    _set_sandbox_envelope_area(model)
    return model


def total_floor_area(model: Mapping[str, Any]) -> float:
    """The dwelling's total floor area, which is not `GroundFloorArea`.

    `GroundFloorArea` is the footprint: 35.6 m2 for the sandbox dwelling, whose
    two storeys give it a total floor area of 71.2 m2. HTC is quoted per square
    metre of the latter.

    Taken from the zone areas FHS actually uses rather than by multiplying the
    footprint by a storey count the model does not state.
    """
    total = 0.0
    zones = model.get("Zone")
    if isinstance(zones, dict):
        for zone in zones.values():
            if not isinstance(zone, dict):
                continue
            for key in ("livingroom_area", "restofdwelling_area"):
                value = zone.get(key)
                if isinstance(value, (int, float)):
                    total += float(value)
    return total


def _stage_sandbox_run(values: Mapping[str, Any], label: str) -> dict[str, Any]:
    """Write the levered sandbox dwelling and its weather into the filesystem.

    Each run gets its own directory. Re-running with the same label would
    otherwise let a previous run's output satisfy the reader when the current
    one wrote nothing.
    """
    configuration = sandbox_configuration_from_values(values)
    authored = sandbox_model_for_configuration(configuration)
    authored_tfa = total_floor_area(authored)
    ground_area = authored.get("GroundFloorArea")
    if not isinstance(ground_area, (int, float)) or ground_area <= 0:
        raise HarnessError("Sandbox model has no positive GroundFloorArea.")
    if authored_tfa <= 0:
        raise HarnessError("Sandbox model has no zone floor areas to scale from.")

    levers = levers_from_attributes(values)

    # The page's control is total floor area, because that is the number a
    # person reads off a plan and the one HLP is quoted against. The lever
    # scales from the footprint. Converting here keeps the ratio the user asked
    # for: a request for the authored 71.2 m2 must leave the dwelling alone, and
    # passing it through unconverted would silently double it.
    requested_tfa = authored_tfa
    if levers.floor_area_m2 is not None:
        requested_tfa = float(levers.floor_area_m2)
        levers = replace(
            levers, floor_area_m2=requested_tfa * ground_area / authored_tfa
        )

    model, applied = apply_shoebox_levers_to_model(authored, levers)
    # Size scaling changes wall lengths and horizontal areas by different
    # powers. Recompute from the configured fabric rather than applying one
    # blanket scale to the infiltration envelope.
    _set_sandbox_envelope_area(model)
    floor_area = requested_tfa

    inputs = WORK_ROOT / label / "inputs"
    if inputs.exists():
        shutil.rmtree(inputs.parent)
    inputs.mkdir(parents=True, exist_ok=True)

    model_path = inputs / "model.json"
    model_path.write_text(json.dumps(model))
    weather_path = inputs / "weather.epw"
    weather_path.write_text(load_sandbox_weather())

    return {
        "model_path": model_path,
        "weather_path": weather_path,
        "floor_area_m2": float(floor_area),
        "applied": applied,
    }


def sandbox_options(
    *,
    model_path: Path,
    weather_path: Path,
    out_dir: Path,
    label: str,
    offsets: tuple[float, ...],
    hem_bin: str,
) -> HtcRunOptions:
    """The options both sandbox runs share.

    Built in one place so the design figure the sliders show and the design
    figure inside an empirical result are produced by the same analysis. If
    these drifted, the two would disagree on screen and neither would be wrong
    in any way the page could detect.
    """
    return HtcRunOptions(
        input_path=model_path,
        weather_path=weather_path,
        out_dir=out_dir,
        label=label,
        offsets=offsets,
        hem_bin=Path(hem_bin),
        # One WASM artifact serves both the core engine and the FHS wrapper,
        # where the native build has two binaries side by side.
        fhs_bin=Path(hem_bin),
        # The browser build is patched by construction; there is no
        # .patch-workdir manifest to verify against.
        require_patched=False,
        # Environment variables do not cross into wasm32-unknown-unknown, so the
        # CLI's HEM_FHS_DISABLE_HEATING_SCHEDULE would be set and then silently
        # ignored. The preprocess route reaches the same place without one.
        fhs_schedule_disable_via="preprocess",
        simulation_period=SANDBOX_PERIOD_HOURS,
    )


def run_sandbox_design(
    *,
    values: Mapping[str, Any],
    run_hem_wasm: Callable[[list[str], str | None, dict[str, str]], None],
    emit: Callable[[dict[str, Any]], None] | None = None,
    hem_bin: str = "/hem/hem",
) -> str:
    """Design HTC for one set of attributes, as a JSON string.

    One run rather than three. Design HTC is read straight off
    `results_static.csv`, so it needs no offsets — and could not use them, since
    the empirical regression needs two points and would reject a single run.
    That is the whole reason this does not go through `run_htc_analysis`:
    everything up to the regression is the same code, driven with the same
    options.
    """
    staged = _stage_sandbox_run(values, "design")
    options = sandbox_options(
        model_path=staged["model_path"],
        weather_path=staged["weather_path"],
        out_dir=WORK_ROOT / "design" / "out",
        label="design",
        offsets=(0.0,),
        hem_bin=hem_bin,
    )

    results_dirs = run_offsets_batch(
        dry_mode=False,
        output_root=options.out_dir,
        batch_label=options.label,
        resolved_input=options.input_path,
        resolved_weather=options.weather_path,
        resolved_external_conditions=None,
        offsets_list=[0.0],
        hem_bin=options.hem_bin,
        fhs_bin=options.fhs_bin,
        detailed_output_heating_cooling=options.detailed_output_heating_cooling,
        fhs_assumptions=options.fhs_assumptions,
        extra_flag=options.extra_flag,
        disable_fhs_heating_schedule=options.disable_fhs_heating_schedule,
        fhs_schedule_disable_via=options.fhs_schedule_disable_via,
        simulation_period=options.simulation_period,
        # The sandbox is always on the preprocess route, so the fixed electric
        # substitution is written onto the wrapper's output. Writing it onto the
        # input instead produces a model the FHS wrapper refuses outright.
        preprocessed_model_transform=model_transform_for(options),
        executor=make_wasm_executor(run_hem_wasm),
        progress=DemoProgress(emit) if emit is not None else None,
    )

    static_values = read_results_static(
        locate_run_files(results_dirs[0.0]).results_static_csv
    )
    htc = compute_design_htc(static_values)
    floor_area = staged["floor_area_m2"]

    return json.dumps(
        {
            "htcWPerK": htc,
            "hlpWPerM2K": htc / floor_area,
            "floorAreaM2": floor_area,
            "applied": staged["applied"],
        }
    )


def run_sandbox_empirical(
    *,
    values: Mapping[str, Any],
    run_hem_wasm: Callable[[list[str], str | None, dict[str, str]], None],
    emit: Callable[[dict[str, Any]], None] | None = None,
    hem_bin: str = "/hem/hem",
) -> str:
    """The three-offset empirical estimate, as a JSON string.

    The weekly series is `htc_w_per_k_by_bin_hours["168"]`, which the same
    analysis already computes for the CLI's report. Binning it in the page
    instead would be a second implementation of an average that has to agree
    with this one.
    """
    staged = _stage_sandbox_run(values, "empirical")
    options = sandbox_options(
        model_path=staged["model_path"],
        weather_path=staged["weather_path"],
        out_dir=WORK_ROOT / "empirical" / "out",
        label="empirical",
        # Empty means the default base +/- 1 K, which is what `h3 run` uses.
        offsets=(),
        hem_bin=hem_bin,
    )

    result = run_htc_analysis(
        options=options,
        config=HarnessConfig(),
        executor=make_wasm_executor(run_hem_wasm),
        progress=DemoProgress(emit) if emit is not None else None,
        write_summary=False,
    )

    summary = result.payload.get("summary", {})
    derived = result.payload.get("derived", {})
    if not isinstance(summary, dict) or not isinstance(derived, dict):
        raise HarnessError("Empirical run returned no summary.")

    weekly = derived.get("htc_w_per_k_by_bin_hours", {})
    weekly_series = weekly.get("168", []) if isinstance(weekly, dict) else []

    design_htc = summary.get("htc_design_w_per_k")
    floor_area = staged["floor_area_m2"]

    return json.dumps(
        {
            "designHtcWPerK": design_htc,
            "empiricalHtcWPerK": summary.get("htc_empirical_w_per_k"),
            "empiricalHtcMaskedWPerK": summary.get("htc_empirical_masked_w_per_k"),
            "weeklyHtcWPerK": list(weekly_series),
            "meanR2": summary.get("mean_r2"),
            "hlpWPerM2K": (
                design_htc / floor_area
                if isinstance(design_htc, (int, float))
                else None
            ),
            "floorAreaM2": floor_area,
            "periodHours": list(SANDBOX_PERIOD_HOURS),
        }
    )
