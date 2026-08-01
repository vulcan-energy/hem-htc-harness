# H3 Docs

`h3` is a Python CLI for estimating empirical HTC (heat transfer coefficient,
W/K) from HEM outputs and comparing the modelled result with a measured target.

This documentation is for people who want to:

- understand the H3 methodology,
- run the main CLI workflows confidently,
- know which flags matter most for `run` and `calibrate`,
- understand what files H3 reads and writes,
- understand how the codebase is organised.

## What H3 does

At a high level, H3:

1. runs a base model and temperature-offset variants,
2. reads the resulting HEM outputs,
3. estimates an empirical HTC from the change in loss power across offsets,
4. writes a machine-readable summary and an HTML report.

The main outputs are:

- `htc_summary.json`: structured results and metadata,
- `htc_report.html`: interactive Plotly report,
- `htc_report_BASELINE.html`: baseline comparison report for calibration runs.

## Quickstart

From the repository root:

```bash
h3 setup
```

This bootstraps the local environment, builds the patched HEM binary, prompts
for paths, and runs `h3 doctor`.

To run a basic analysis with EPW weather:

```bash
h3 run --input model.json --weather weather.epw
```

To use an `ExternalConditions` JSON instead:

```bash
h3 run --input model.json --external-conditions external_conditions.json
```

To calibrate against a measured HTC target:

```bash
h3 calibrate \
  --input model.json \
  --external-conditions ec.json \
  --measured-htc measured.json
```

## Which page to read next

- Start with [Methodology](methodology.md) if you want the calculation logic.
- Go to [CLI Usage](cli.md) for the workflow-level commands.
- Use [Flags Reference](flags.md) when you need to understand a specific flag.
- Read [Outputs](outputs.md) to understand the files H3 writes.
- Read [Browser demo](demo.md) to see H3 run client-side, with no install.
- Read [Codebase](codebase.md) if you want the technical architecture and caveats.

## Preview locally

From the repository root:

```bash
. .venv/bin/activate
pip install -e ".[docs]"
mkdocs serve
```

Then open `http://127.0.0.1:8000`.

## Inputs at a glance

Typical runs use:

- a base HEM model JSON,
- either an EPW file or an `ExternalConditions` JSON,
- optional measured HTC metadata for comparison or calibration.

Per run, H3 requires these HEM output files:

- `results_heat_balance_air_node.csv`,
- `results_static.csv`,
- `results_heat_balance_internal_boundary.csv`, unless `--no-fihc` is used.

`results.csv` is optional. Without it H3 still computes HTC, but the masking and
temperature series that depend on it are omitted, and `--heating-only` cannot be
used.

## Scope of the docs

These docs focus on the public, user-facing surface:

- methodology,
- command workflows,
- flags and defaults,
- outputs and file meanings.

It does not try to document every internal module in the codebase.
