# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.

"""Run orchestration, independent of any user interface.

This is the layer the CLI and the browser demo share. It imports no `click` and
starts no subprocess of its own: HEM execution goes through the injectable
executor in `runner`, and user-visible progress goes through a
`ProgressReporter` supplied by the caller.
"""

from __future__ import annotations

import csv
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from .errors import HarnessError
from .paths import expected_results_dirs, format_offset_label, prepare_run_dir
from .reader import (
    adapt_preprocessed_model_for_core,
    flatten_fhs_heating_schedule_to_model,
    set_simulation_period_on_model,
)
from .runner import HemExecutor, HemRunRequest, run_hem


class ProgressReporter(Protocol):
    """Receives progress events from a batch run.

    The CLI implementation prints and spins; the browser demo posts messages to
    the host page. The library itself stays silent.
    """

    def note(self, message: str) -> None: ...

    def batch_start(self, label: str, total: int) -> None: ...

    def run_start(
        self, label: str, index: int, total: int, offset_k: float
    ) -> None: ...

    def run_complete(
        self, label: str, index: int, total: int, elapsed_s: float
    ) -> None: ...


class NullProgressReporter:
    """Discards progress events. The default for library callers."""

    def note(self, message: str) -> None:
        return

    def batch_start(self, label: str, total: int) -> None:
        return

    def run_start(self, label: str, index: int, total: int, offset_k: float) -> None:
        return

    def run_complete(
        self, label: str, index: int, total: int, elapsed_s: float
    ) -> None:
        return


#: How `disable_fhs_heating_schedule` is delivered to the engine.
#:
#: ``"env"`` sets ``HEM_FHS_DISABLE_HEATING_SCHEDULE``, which the patched
#: wrapper reads. ``"preprocess"`` runs the wrapper for its preprocessing only,
#: flattens the heating controls it produced, and runs the core engine on the
#: result — the same effect, against a pristine wrapper and with no environment
#: variable. The browser needs ``"preprocess"``, because environment variables
#: do not cross into a ``wasm32-unknown-unknown`` build and the WASM build
#: refuses a patched wrapper submodule.
FhsScheduleDisableVia = Literal["env", "preprocess"]


def resolve_engine_env_overrides(
    *,
    env_overrides: dict[str, str] | None,
    disable_fhs_heating_schedule: bool,
    fhs_schedule_disable_via: FhsScheduleDisableVia = "env",
) -> dict[str, str]:
    env = dict(env_overrides) if env_overrides else {}
    if disable_fhs_heating_schedule and fhs_schedule_disable_via == "env":
        env["HEM_FHS_DISABLE_HEATING_SCHEDULE"] = "1"
    return env


def run_offsets_batch(
    *,
    dry_mode: bool,
    output_root: Path,
    batch_label: str,
    resolved_input: Path,
    resolved_weather: Path | None,
    resolved_external_conditions: Path | None,
    offsets_list: list[float],
    hem_bin: Path | None,
    fhs_bin: Path | None = None,
    detailed_output_heating_cooling: bool,
    fhs_assumptions: bool,
    extra_flag: tuple[str, ...],
    disable_fhs_heating_schedule: bool,
    fhs_schedule_disable_via: FhsScheduleDisableVia = "env",
    simulation_period: tuple[float, float, float] | None = None,
    env_overrides: dict[str, str] | None = None,
    model_transform: Callable[[dict[str, object]], dict[str, object]] | None = None,
    # Applied to the wrapper's preprocessed model instead of the authored one.
    # The FHS and core schemas disagree about some objects, so a transform whose
    # result the core engine validates cannot always be written before the
    # wrapper runs. Only meaningful on the preprocess route.
    preprocessed_model_transform: (
        Callable[[dict[str, object]], dict[str, object]] | None
    ) = None,
    executor: HemExecutor | None = None,
    progress: ProgressReporter | None = None,
) -> dict[float, Path]:
    """Run the base and offset models, returning the results directory per offset.

    Shared by `run` and `calibrate`. In dry mode no HEM run happens; existing
    results directories are located and validated instead.
    """
    progress = progress or NullProgressReporter()

    if dry_mode:
        results_dirs = expected_results_dirs(
            output_root=output_root,
            label=batch_label,
            input_stem=resolved_input.stem,
            offsets_list=offsets_list,
        )
        for offset, results_dir in results_dirs.items():
            if not results_dir.exists():
                raise HarnessError(
                    f"Missing results for offset {offset:+.3f}K: {results_dir}"
                )
        return results_dirs

    output_root.mkdir(parents=True, exist_ok=True)
    n_offsets = len(offsets_list)
    progress.batch_start(batch_label, n_offsets)

    # FHS assumptions are not a core-engine flag. From engine v1.0.0-alpha the
    # wrapper is a separate binary, selected via HemRunRequest.fhs_assumptions
    # and invoked with `--mode actual`.
    extra_flags = list(extra_flag)
    env = resolve_engine_env_overrides(
        env_overrides=env_overrides,
        disable_fhs_heating_schedule=disable_fhs_heating_schedule,
        fhs_schedule_disable_via=fhs_schedule_disable_via,
    )

    via_preprocess = (
        disable_fhs_heating_schedule
        and fhs_schedule_disable_via == "preprocess"
        and fhs_assumptions
    )

    results_dirs: dict[float, Path] = {}
    for i, offset in enumerate(offsets_list, start=1):
        run_label = format_offset_label(batch_label, offset)
        input_copy, weather_copy, results_dir = prepare_run_dir(
            output_root,
            run_label,
            resolved_input,
            resolved_weather,
            resolved_external_conditions,
            offset,
            model_transform,
        )
        weather_for_run = (
            None if resolved_external_conditions is not None else weather_copy
        )

        def _request(
            input_path: Path,
            *,
            weather_path: Path | None,
            fhs: bool,
            preprocess_only: bool = False,
        ) -> HemRunRequest:
            return HemRunRequest(
                input_path=input_path,
                weather_path=weather_path,
                hem_bin=hem_bin,
                heat_balance=True,
                detailed_output_heating_cooling=detailed_output_heating_cooling,
                extra_flags=extra_flags,
                dry_run=False,
                working_dir=None,
                env_overrides=env or None,
                fhs_assumptions=fhs,
                fhs_bin=fhs_bin,
                preprocess_only=preprocess_only,
            )

        progress.run_start(run_label, i, n_offsets, offset)
        start = time.perf_counter()

        if via_preprocess:
            run_hem(
                _request(
                    input_copy,
                    weather_path=weather_for_run,
                    fhs=True,
                    preprocess_only=True,
                ),
                executor=executor,
            )
            core_input = _write_flattened_core_input(
                results_dir=results_dir,
                input_copy=input_copy,
                simulation_period=simulation_period,
                preprocessed_model_transform=preprocessed_model_transform,
            )
            # The wrapper baked the external conditions into the preprocessed
            # model, offset and all, so the core run must not be handed the
            # weather file again.
            run_hem(
                _request(core_input, weather_path=None, fhs=False),
                executor=executor,
            )
            results_dir = core_input.parent / f"{core_input.stem}__results"
        else:
            run_hem(
                _request(input_copy, weather_path=weather_for_run, fhs=fhs_assumptions),
                executor=executor,
            )

        elapsed = time.perf_counter() - start
        progress.run_complete(run_label, i, n_offsets, elapsed)

        results_dirs[offset] = results_dir

    return results_dirs


def _write_flattened_core_input(
    *,
    results_dir: Path,
    input_copy: Path,
    simulation_period: tuple[float, float, float] | None = None,
    preprocessed_model_transform: (
        Callable[[dict[str, object]], dict[str, object]] | None
    ) = None,
) -> Path:
    """Turn the wrapper's preprocessed model into a core-engine input.

    Reads `<key>__preproc.json` from the preprocess-only run, holds the heating
    controls at setpoint, drops the one field the core schema rejects, and
    writes the result beside the original input. The new file gets its own name
    so both the model the wrapper saw and the model the core engine ran stay on
    disk and can be diffed.

    `simulation_period` is `(start_hour, end_hour, step_hours)` and shortens the
    run. It belongs here rather than earlier because the wrapper writes
    `SimulationTime` itself while preprocessing; this is the first point at
    which overriding it survives.
    """
    candidates = sorted(results_dir.glob("*__preproc.json"))
    if not candidates:
        raise HarnessError(
            f"The FHS preprocess-only run wrote no model to {results_dir}.\n\n"
            "h3 asked the wrapper for its preprocessed model so it could "
            "disable the heating schedule without an environment variable.\n\n"
            "Fix:\n"
            "- Check the wrapper binary supports --preprocess-only.\n"
            "- Or run with the environment-variable route instead."
        )

    preprocessed = json.loads(candidates[0].read_text())
    flattened, info = flatten_fhs_heating_schedule_to_model(preprocessed)
    if not info["controls_flattened"]:
        raise HarnessError(
            f"No FHS heating pattern to flatten in {candidates[0].name}.\n\n"
            "h3 expected the wrapper's preprocessing to have written a "
            "SetpointTimeControl referenced by a SpaceHeatSystem. Running the "
            "core engine on this model would silently keep the standard "
            "heating schedule, which is the thing this route exists to remove."
        )

    # After flattening, so a substituted emitter inherits the control that was
    # just held at setpoint; before the core adaptation, so anything it writes
    # is still checked against the core schema's expectations.
    if preprocessed_model_transform is not None:
        flattened = preprocessed_model_transform(flattened)

    core_ready, _ = adapt_preprocessed_model_for_core(flattened)
    if simulation_period is not None:
        start_hour, end_hour, step_hours = simulation_period
        core_ready, _ = set_simulation_period_on_model(
            core_ready,
            start_hour=start_hour,
            end_hour=end_hour,
            step_hours=step_hours,
        )
    core_input = input_copy.with_name(f"{input_copy.stem}__preproc_flat.json")
    core_input.write_text(json.dumps(core_ready))
    return core_input


def read_fee_metric_from_results(results_dir: Path) -> float | None:
    """Read Fabric Energy Efficiency (kWh/m2.yr) from FEE postproc output."""
    postproc_candidates = sorted(results_dir.glob("*__FHS_FEE__postproc.csv"))
    if not postproc_candidates:
        return None
    csv_path = postproc_candidates[0]
    try:
        with csv_path.open(newline="") as handle:
            reader = csv.reader(handle)
            for row in reader:
                if len(row) < 3:
                    continue
                if row[0].strip().lower() != "fabric energy efficiency":
                    continue
                return float(row[2])
    except (OSError, ValueError):
        return None
    return None
