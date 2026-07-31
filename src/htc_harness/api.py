# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.

"""The h3 library entry point.

`run_htc_analysis` is the whole `h3 run` pipeline as a single callable, with no
`click` and no `subprocess` of its own. The CLI is one caller; the browser demo
is another, supplying a WASM-backed executor because Pyodide has no subprocess.

    from htc_harness.api import HtcRunOptions, run_htc_analysis

    result = run_htc_analysis(
        options=HtcRunOptions(input_path=..., weather_path=...),
        config=config,
        executor=my_wasm_executor,
    )
    result.payload["masked_aggregate_w_per_k"]
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import HarnessConfig
from .empirical import analyze_offsets
from .errors import HarnessError, HarnessUsageError
from .paths import (
    DEFAULT_OFFSETS_K,
    default_run_output_root,
    parse_window,
    resolve_and_validate_inputs,
)
from .provenance import attach_engine_provenance_to_payload
from .reader import (
    apply_fixed_instant_electric_heating_to_model,
    apply_fixed_instant_electric_heating_to_preprocessed_model,
    normalise_external_conditions,
    read_external_conditions_file,
)
from .runner import HemExecutor
from .workflow import (
    FhsScheduleDisableVia,
    NullProgressReporter,
    ProgressReporter,
    resolve_engine_env_overrides,
    run_offsets_batch,
)


@dataclass
class HtcRunOptions:
    """Everything `h3 run` accepts, independent of how it was supplied.

    These defaults are the defaults, for every caller. `cli.py` reads them
    rather than restating them, so the CLI and the browser demo cannot drift
    into running different analyses from the same inputs.
    """

    input_path: Path
    weather_path: Path | None = None
    external_conditions_path: Path | None = None
    out_dir: Path | None = None
    summary_path: Path | None = None
    label: str = "run"
    offsets: tuple[float, ...] = ()
    include_base: bool = True
    hem_bin: Path | None = None
    timestep_hours: float = 0.5
    window: str | None = None
    heating_only: bool = False
    include_fihc: bool = True
    measured_htc_path: Path | None = None
    detailed_output_heating_cooling: bool = False
    fhs_assumptions: bool = True
    # Explicit FHS wrapper path; derived from hem_bin when unset.
    fhs_bin: Path | None = None
    disable_fhs_heating_schedule: bool = True
    # How that disabling is delivered. "env" sets the environment variable the
    # patched wrapper reads. "preprocess" asks the wrapper for its
    # preprocessed model, flattens the heating controls in Python, and runs the
    # core engine on the result — same effect, no environment variable, no
    # patched wrapper. See `workflow.FhsScheduleDisableVia`.
    fhs_schedule_disable_via: FhsScheduleDisableVia = "env"
    # Shorten the simulated period, as (start_hour, end_hour, step_hours) from
    # the start of the year. Only meaningful on the "preprocess" route, which is
    # the first point at which the wrapper's own SimulationTime can be
    # overridden. `None` runs whatever period the wrapper resolved.
    simulation_period: tuple[float, float, float] | None = None
    extra_flag: tuple[str, ...] = ()
    require_patched: bool = True
    fixed_electric_heating: bool = True
    fixed_electric_heater_kw_per_zone: float = 20.0
    dry_mode: bool = False
    env_overrides: dict[str, str] = field(default_factory=dict)
    # Pre-simulation weather normalisation. Requires external conditions;
    # an EPW cannot be normalised in place.
    flatten_solar: bool = False
    remove_solar: bool = False
    flatten_wind: bool = False
    remove_wind: bool = False


@dataclass
class HtcRunResult:
    """Outcome of a run: the summary payload and where things landed."""

    payload: dict[str, object]
    summary_path: Path
    output_root: Path
    results_dirs: dict[float, Path]


def resolve_offsets(offsets: tuple[float, ...], *, include_base: bool) -> list[float]:
    """Build the offset list, with the base run first when requested."""
    offsets_list = list(offsets) if offsets else list(DEFAULT_OFFSETS_K)
    if include_base and 0.0 not in offsets_list:
        offsets_list.insert(0, 0.0)
    return offsets_list


class FixedElectricHeatingTransform:
    """Swap each zone's heating for a fixed instant electric heater.

    A callable object rather than a closure so the info it produces can be read
    back off `.info` after the run, and so callers that drive
    `run_offsets_batch` directly — the browser sandbox's design run, which has a
    single offset and so cannot go through the empirical regression — apply the
    same transform as `run_htc_analysis` rather than their own copy of it.

    `after_preprocess` selects which of the two substitutions to make. They are
    not interchangeable: the FHS input schema and the core schema disagree about
    what an `InstantElecHeater` object contains, so the shape depends on which
    of them will validate the result. See the two reader functions.

    Only the first run's info is kept: every offset transforms the same model,
    so they agree, and the payload wants one record rather than a list.
    """

    def __init__(
        self, *, rated_power_kw_per_zone: float, after_preprocess: bool
    ) -> None:
        self.rated_power_kw_per_zone = rated_power_kw_per_zone
        self.after_preprocess = after_preprocess
        self.info: dict[str, object] | None = None

    def __call__(self, model: dict[str, object]) -> dict[str, object]:
        apply = (
            apply_fixed_instant_electric_heating_to_preprocessed_model
            if self.after_preprocess
            else apply_fixed_instant_electric_heating_to_model
        )
        transformed, info = apply(
            model, rated_power_kw_per_zone=self.rated_power_kw_per_zone
        )
        if self.info is None:
            self.info = info
        return transformed


def uses_preprocess_route(options: HtcRunOptions) -> bool:
    """Whether the wrapper's preprocessed model is what the core engine runs."""
    return (
        options.fhs_assumptions
        and options.disable_fhs_heating_schedule
        and options.fhs_schedule_disable_via == "preprocess"
    )


def model_transform_for(
    options: HtcRunOptions,
) -> FixedElectricHeatingTransform | None:
    """The model transform a set of options implies, or None for no transform."""
    if not options.fixed_electric_heating:
        return None
    return FixedElectricHeatingTransform(
        rated_power_kw_per_zone=options.fixed_electric_heater_kw_per_zone,
        after_preprocess=uses_preprocess_route(options),
    )


def _normalisation_labels(options: HtcRunOptions) -> list[str]:
    return [
        label
        for label, enabled in (
            ("flatten-solar", options.flatten_solar),
            ("remove-solar", options.remove_solar),
            ("flatten-wind", options.flatten_wind),
            ("remove-wind", options.remove_wind),
        )
        if enabled
    ]


def apply_weather_normalisation(
    *,
    options: HtcRunOptions,
    external_conditions_path: Path,
    output_root: Path,
) -> tuple[Path, list[str]]:
    """Write a normalised copy of the external conditions and return its path.

    The normalised file goes into the output directory rather than beside the
    original, so a shared inputs folder is never polluted.
    """
    labels = _normalisation_labels(options)
    ec = normalise_external_conditions(
        read_external_conditions_file(external_conditions_path),
        flatten_solar=options.flatten_solar,
        remove_solar=options.remove_solar,
        flatten_wind=options.flatten_wind,
        remove_wind=options.remove_wind,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    normalised_path = output_root / (
        external_conditions_path.stem + f"__{'_'.join(labels)}.json"
    )
    normalised_path.write_text(json.dumps({"ExternalConditions": ec}, indent=2))
    return normalised_path, labels


def run_htc_analysis(
    *,
    options: HtcRunOptions,
    config: HarnessConfig,
    executor: HemExecutor | None = None,
    progress: ProgressReporter | None = None,
    write_summary: bool = True,
) -> HtcRunResult:
    """Run the base and offset models, then estimate empirical HTC.

    Set `write_summary=False` to get the payload back without touching disk,
    which is what an in-browser caller wants.
    """
    progress = progress or NullProgressReporter()

    if _normalisation_labels(options) and options.weather_path is not None:
        raise HarnessUsageError(
            "Solar/wind normalisation requires --external-conditions (not --weather)."
        )

    # Fail rather than silently run the wrapper's own period. The override is
    # applied to the wrapper's preprocessed model, so there is nowhere to put it
    # on any other route, and a run that quietly covered a whole year when the
    # caller asked for three months would be wrong in a way nothing reports.
    if options.simulation_period is not None and not (
        options.disable_fhs_heating_schedule
        and options.fhs_schedule_disable_via == "preprocess"
        and options.fhs_assumptions
    ):
        raise HarnessUsageError(
            "simulation_period requires the FHS preprocess route "
            "(fhs_assumptions, disable_fhs_heating_schedule, "
            'fhs_schedule_disable_via="preprocess").'
        )

    resolved_input, resolved_weather, resolved_external_conditions = (
        resolve_and_validate_inputs(
            config=config,
            input_path=options.input_path,
            weather_path=options.weather_path,
            external_conditions_path=options.external_conditions_path,
            dry_mode=options.dry_mode,
        )
    )

    output_root = default_run_output_root(config, resolved_input, options.out_dir)
    offsets_list = resolve_offsets(options.offsets, include_base=options.include_base)

    # Order matters: malformed options are reported before missing output, so a
    # bad --window still exits 2 rather than being masked by a dry-mode error.
    start_idx, end_idx = parse_window(options.window)
    summary_path = options.summary_path or (output_root / "htc_summary.json")

    if options.dry_mode and not output_root.exists():
        raise HarnessError(
            f"Output directory not found (nothing to analyse): {output_root}"
        )

    if _normalisation_labels(options) and resolved_external_conditions is not None:
        resolved_external_conditions, labels = apply_weather_normalisation(
            options=options,
            external_conditions_path=resolved_external_conditions,
            output_root=output_root,
        )
        progress.note(
            f"Weather normalised ({', '.join(labels)}): "
            f"{resolved_external_conditions.name}"
        )

    model_transform = model_transform_for(options)
    # On the preprocess route the substitution is written onto the wrapper's
    # output, not its input, so it goes in the other slot.
    after_preprocess = model_transform is not None and model_transform.after_preprocess

    results_dirs = run_offsets_batch(
        dry_mode=options.dry_mode,
        output_root=output_root,
        batch_label=options.label,
        resolved_input=resolved_input,
        resolved_weather=resolved_weather,
        resolved_external_conditions=resolved_external_conditions,
        offsets_list=offsets_list,
        hem_bin=options.hem_bin,
        fhs_bin=options.fhs_bin,
        detailed_output_heating_cooling=options.detailed_output_heating_cooling,
        fhs_assumptions=options.fhs_assumptions,
        extra_flag=options.extra_flag,
        disable_fhs_heating_schedule=options.disable_fhs_heating_schedule,
        fhs_schedule_disable_via=options.fhs_schedule_disable_via,
        simulation_period=options.simulation_period,
        env_overrides=options.env_overrides or None,
        model_transform=None if after_preprocess else model_transform,
        preprocessed_model_transform=model_transform if after_preprocess else None,
        executor=executor,
        progress=progress,
    )

    payload = analyze_offsets(
        results_dirs_by_offset_k=results_dirs,
        timestep_hours=options.timestep_hours,
        start_idx=start_idx,
        end_idx=end_idx,
        heating_only=options.heating_only,
        measured_htc_path=options.measured_htc_path,
        include_fihc=options.include_fihc,
    )

    if options.fixed_electric_heating:
        payload.setdefault("job", {})
        if isinstance(payload["job"], dict):
            payload["job"]["fixed_electric_heating"] = (
                model_transform.info
                if model_transform is not None and model_transform.info is not None
                else {
                    "rated_power_kw_per_zone": (
                        options.fixed_electric_heater_kw_per_zone
                    ),
                }
            )

    attach_engine_provenance_to_payload(
        payload,
        hem_bin=options.hem_bin,
        require_patched=options.require_patched,
        env_overrides=resolve_engine_env_overrides(
            env_overrides=None,
            disable_fhs_heating_schedule=options.disable_fhs_heating_schedule,
            fhs_schedule_disable_via=options.fhs_schedule_disable_via,
        ),
        extra_flags=list(options.extra_flag),
    )

    if write_summary:
        summary_path.write_text(json.dumps(payload, indent=2))

    return HtcRunResult(
        payload=payload,
        summary_path=summary_path,
        output_root=output_root,
        results_dirs=results_dirs,
    )
