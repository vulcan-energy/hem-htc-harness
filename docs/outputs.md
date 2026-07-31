# Outputs

H3 writes both machine-readable and human-readable outputs. This page explains
the main files you should expect from `run`, `calibrate`, and `plot`.

## Main output files

### `htc_summary.json`

This is the main structured output from H3.

It contains:

- derived loss and system signals,
- empirical HTC summaries,
- metadata about the run,
- optional measured HTC metadata,
- engine provenance attached under the job payload.

This is the file to use if you want to:

- inspect results programmatically,
- archive run metadata,
- regenerate plots later with `h3 plot`,
- compare multiple runs in a scripted workflow.

### `htc_report.html`

This is the main interactive report written by `h3 run`, `h3 calibrate`, or
`h3 plot`.

It is intended for quick visual inspection and typically includes:

- HTC summaries across aggregations,
- loss component breakdowns,
- masking-aware views,
- diagnostic plots and residual views.

### `htc_report_BASELINE.html`

This report is written by `h3 calibrate` and shows the baseline comparison for
the calibration workflow.

### `<summary_stem>_baseline.json`

This baseline summary file is also written during calibration so you can compare
pre-calibration and post-calibration outputs directly.

## Output layout

By default H3 writes under:

`<output_dir>/<input_stem>/...`

Within that tree, runs typically use folders such as:

- `run_base`,
- `run_plus1K`,
- `run_minus1K`,
- calibration-specific folders based on the chosen label.

You can override the base location per invocation with `--out-dir`.

## Files H3 reads from a completed run

When analysing or re-plotting completed outputs, H3 requires:

- `results_heat_balance_air_node.csv`,
- `results_static.csv`,
- `results_heat_balance_internal_boundary.csv`, unless `--no-fihc` is used.

`results.csv` is optional but recommended. Without it, HTC is still computed,
but masking has no unmet-demand series to work from, the internal-temperature
series is absent, and `--heating-only` cannot be used.

These are the sources H3 uses to derive the empirical HTC and diagnostic
signals.

## Important `htc_summary.json` fields

Useful fields to know:

| Field | Meaning |
|---|---|
| `summary.htc_empirical_w_per_k` | Mean HTC over the selected analysis window. |
| `derived.q_loss_w_by_offset_k` | Loss power by offset, including FIHC when enabled. |
| `derived.q_loss_components_w_by_offset_k` | Loss components by offset. |
| `derived.q_system_total_w_by_offset_k` | Total system gains by offset. |
| `derived.q_system_fabric_int_heat_cool_w_by_offset_k` | Raw FIHC-related fabric contribution. |
| `derived.temperature_series.external_air_temp_c` | External air temperature series used in the analysis. |

[Methodology](methodology.md#key-fields-in-htc_summaryjson) has the fuller
field-by-field table.

## Engine provenance

H3 also stores engine provenance inside the output payload so runs are easier to
reproduce.

That includes:

- the Rust engine implementation,
- whether patched-binary verification was required,
- the binary used,
- forwarded environment overrides and extra flags,
- patched manifest details when available.

## Choosing the right output

Use:

- `htc_summary.json` when you want structured data,
- `htc_report.html` when you want to visually inspect a single result,
- `htc_report_BASELINE.html` and baseline JSON when you want to understand what
  changed during calibration.
