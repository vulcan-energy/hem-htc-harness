# Codebase

This page explains how the H3 codebase is organised and where the main logic
lives. It is intentionally more technical than the user-facing pages.

## The shape that matters

H3 is a **library with two front ends**, not a CLI with helper modules. The
run pipeline imports no `click` and starts no subprocess of its own, which is
what lets the same code run in a browser under Pyodide.

```
h3 CLI ─┐
        ├──▶ api.run_htc_analysis ──▶ workflow ──▶ runner ──▶ HemExecutor ──▶ HEM
demo  ──┘                                │                        │
                                         │              subprocess (CLI)
                                         │              WASM call  (browser)
                                         ▼
                              reader ──▶ empirical ──▶ htc_summary.json
                                                            │
                                                            ▼
                                                        plotting
```

The seam is `HemExecutor` in `runner.py`: HEM execution is injected rather than
shelled out. `tests/test_api.py` asserts the run pipeline never launches a
subprocess, so a shell-out added to this path fails in CI rather than at the
Pyodide boundary.

`h3 calibrate` is the exception — it drives `workflow.run_offsets_batch` from
`cli.py` directly rather than through `api.py`, because it needs to re-run the
batch many times with different model transforms.

## Main modules

### `src/htc_harness/api.py`

The library entry point. `run_htc_analysis` is the whole `h3 run` pipeline as a
single callable, and `HtcRunOptions` holds **every default** — the CLI reads
them from here rather than restating them, so the CLI and the browser demo
cannot drift into running different analyses.

If you want to know what a default run does, start here.

### `src/htc_harness/cli.py`

The `click` layer: option parsing, config overrides, progress output, and the
`setup` / `doctor` / `build-hem` commands that have no library equivalent. It
translates `HarnessError` and friends back into `click` exceptions so exit codes
stay stable.

It is a front end, not the orchestrator. The exception is `calibrate`, which
still lives here.

### `src/htc_harness/workflow.py`

Run orchestration with no user interface: runs the base and offset cases,
reporting progress through a caller-supplied `ProgressReporter`. Shared by `run`
and `calibrate`.

### `src/htc_harness/paths.py`

Path resolution, window parsing and run-directory staging — copying inputs,
applying the temperature offset, merging `ExternalConditions` into the model.

### `src/htc_harness/runner.py`

A thin adapter around the Rust HEM command line. It builds the command and
executes it through a `HemExecutor`.

From engine v1.0.0-alpha the FHS wrapper is a separate binary, located relative
to the engine binary.

### `src/htc_harness/reader.py`

File discovery, CSV parsing and model transforms: locating HEM output files,
reading timeseries, validating measured-HTC payloads, and applying the
fixed-electric-heating and fabric-scalar transforms.

### `src/htc_harness/empirical.py`

The core analysis: loss-term extraction, per-timestep and per-block HTC,
masking, aggregation, and summary-payload generation. The most important file in
the codebase for understanding the calculation itself.

### `src/htc_harness/calibration.py`

The calibration solve and its iterative refinement, driven by callbacks from
`cli.py` so it stays free of `click`.

### `src/htc_harness/browser.py`

The Pyodide entry point for the fixed synthetic sandbox. It applies a bounded
dwelling configuration and seven numeric controls to the bundled model, then
runs design or empirical HTC with a WASM-backed executor. It does not accept
arbitrary model files. See `demo/README.md`.

### `src/htc_harness/plotting/`

The reporting layer: `loader.py` loads `htc_summary.json`, `figures.py` builds
Plotly figures, `report.py` assembles the HTML, `utils.py` holds shared helpers,
`models.py` the report-facing data models.

### Supporting modules

- `config.py` — the per-user config in `~/.config/htc-harness/config.json`
- `provenance.py` — engine metadata attached to every summary
- `errors.py` — library exceptions the CLI translates into `click` ones
- `build_tools.py` — cloning and patching the pinned engine for `h3 build-hem`;
  it separates packaged read-only patch resources from the writable build cache

## End-to-end data flow

### `h3 run`

1. `cli.py` parses options and resolves config overrides
2. `api.run_htc_analysis` resolves and validates the input triple
3. weather is optionally normalised into the output directory
4. the model is optionally transformed to fixed electric heating
5. `workflow.run_offsets_batch` runs base and offset cases
6. `reader` locates output CSVs; `empirical` derives the series and summaries
7. `htc_summary.json` is written, then optionally `htc_report.html`

### `h3 calibrate`

1. run a baseline case and derive the masked monthly baseline metric
2. run a global probe at `1 + delta_scalar` and estimate sensitivity
3. solve the shared scalar and rerun with it
4. optionally refine iteratively
5. write calibrated and baseline outputs and their comparison reports

## Important implementation details

### Defaults live in `HtcRunOptions`

`cli.py` builds its `click` defaults from `dataclasses.fields(HtcRunOptions)`.
Restating a default in a decorator is how the browser demo previously ended up
running a different analysis from `h3 run`.

### Path resolution is config-aware

User-supplied paths are checked as: absolute path, then the configured base
directory, then the current working directory, otherwise passed through to fail
validation with a clear error. That is why short filenames work after `h3 setup`.

### Plotting uses the summary JSON, not raw CSVs

`h3 plot` does not re-run the analysis from HEM CSVs. It loads
`htc_summary*.json` through `plotting/loader.py` and renders from that payload.

Both share their analysis primitives with `empirical.py` so the chart and the
JSON agree. One deliberate exception is documented in
[Methodology](methodology.md#masking-and-block-filtering): block gating bins on
calendar boundaries in the report and uniformly in the summary.

## Critical notes and caveats

### The CLI is the source of truth for available flags

The safest reference for flag behaviour is `python -m htc_harness.cli <command>
--help` and `src/htc_harness/cli.py`. [Flags Reference](flags.md) should stay
close to those sources.

### Measured HTC schema is broader than current analysis behaviour

The schema accepts both index-based and `date_range` windows, but the analysis
is index-based. Schema validation is therefore slightly more permissive than the
calculation pipeline.

### Calibration uses one shared scalar

One scalar drives both the fabric transform and `HEM_ACH_CAL_FACTOR`, so
calibration does not separately identify ventilation and fabric effects. That is
a real limitation of the method, not a naming artefact.

### `calibrate` requires a patched engine

Both `run` and `calibrate` use the pinned Rust HEM/FHS build. Calibration also
requires h3's patches because it depends on their environment-variable hooks.

### Reports simplify the underlying internals

The HTML report is designed for interpretation, not for exposing every internal
series. For exact downstream use, treat `htc_summary.json` as canonical.

## Where to look next

- Read [Methodology](methodology.md) for the calculation itself.
- Read [CLI Usage](cli.md) for command workflows.
- Read [Outputs](outputs.md) to understand the generated artifacts.
- Read [Browser demo](demo.md) for how h3 runs client-side.
