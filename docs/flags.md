# Flags Reference

This page groups the most important H3 flags by workflow instead of listing
every option alphabetically.

## Global flags

These can be supplied before the command name:

| Flag | Meaning |
|---|---|
| `--config PATH` | Use a non-default config file instead of `~/.config/htc-harness/config.json`. Also settable through the `HTC_HARNESS_CONFIG` environment variable; the flag wins when both are set. |
| `--input-dir PATH` | Override the configured input directory for this invocation. |
| `--weather-dir PATH` | Override the configured weather directory for this invocation. |
| `--external-conditions-dir PATH` | Override the configured external-conditions directory for this invocation. |
| `--output-dir PATH` | Override the configured output directory for this invocation. |

## `h3 run`

!!! warning "Two defaults change your results"

    `h3 run` defaults to `--fixed-electric-heating`, which **replaces every
    zone's heating system** with a 20 kW `InstantElecHeater` before running, and
    to `--fhs-assumptions`, which runs the FHS wrapper binary rather than the
    core engine. Both are deliberate — they give the offset runs a heat source
    that can always meet demand — but neither is a no-op. Pass
    `--no-fixed-electric-heating` / `--no-fhs-assumptions` to run the model as
    written.

Defaults below are the shipped values. They live in `HtcRunOptions`
(`src/htc_harness/api.py`), which is what the CLI reads, so `--help` and the
library always agree.

### Core inputs

| Flag | Meaning |
|---|---|
| `--input PATH` | Base model JSON to run. |
| `--weather PATH` | EPW weather file. |
| `--external-conditions PATH` | `ExternalConditions` JSON input. Mutually exclusive with `--weather`. |
| `--out-dir PATH` | Override the output base directory. |
| `--label TEXT` | Prefix for the run folder under the output directory. |
| `--offset FLOAT` | Temperature offset in K. Repeatable. Defaults to `-1.0` and `+1.0`. |
| `--out PATH` | Explicit output summary JSON path. |

### Engine and model controls

| Flag | Meaning |
|---|---|
| `--hem-bin PATH` | Path to the Rust HEM binary. |
| `--fhs-assumptions / --no-fhs-assumptions` | Run via the patched FHS wrapper binary (`--mode actual`) instead of the core engine. Default `--fhs-assumptions`. |
| `--disable-fhs-heating-schedule / --no-disable-fhs-heating-schedule` | Disable the patched FHS heating schedule, by setting `HEM_FHS_DISABLE_HEATING_SCHEDULE=1`. Default enabled. |
| `--extra-flag TEXT` | Forward an additional raw flag to the HEM engine. Repeatable. |
| `--require-patched / --no-require-patched` | Require the Rust binary to have been built through `h3 build-hem`. Default `--require-patched`. |

### Analysis window and masking

| Flag | Meaning |
|---|---|
| `--dry` | Analyse existing outputs without re-running simulations. |
| `--timestep-hours FLOAT` | Timestep size used by the analysis. Default `0.5`. |
| `--window start_idx,end_idx` | Limit analysis to a manual index window. Default: the full run. |
| `--heating-only / --no-heating-only` | Restrict analysis to timesteps with space-heating demand. Default `--no-heating-only`. Requires `results.csv`. |
| `--measured-htc PATH` | Optional measured HTC JSON for comparison. |

### Weather normalisation

| Flag | Meaning |
|---|---|
| `--flatten-solar` | Replace hourly solar irradiance with the annual mean. |
| `--remove-solar` | Set solar irradiance to zero. |
| `--flatten-wind` | Replace hourly wind speed with the annual mean. |
| `--remove-wind` | Set wind speed to zero. |

Notes:

- solar flags are mutually exclusive with each other,
- wind flags are mutually exclusive with each other,
- these options require `--external-conditions`, not `--weather`.

### HTC interpretation and outputs

| Flag | Meaning |
|---|---|
| `--include-fihc / --no-fihc` | Include the FIHC correction in `q_loss` and `FabricHeatLoss`. Default `--include-fihc`; see [Methodology](methodology.md#fabricintheatcool-fihc). |
| `--fixed-electric-heating / --no-fixed-electric-heating` | Replace **every** zone's heating system with a fixed-size `InstantElecHeater` before running. Default **enabled**. |
| `--fixed-electric-heater-kw-per-zone FLOAT` | Heater size used when fixed electric heating is enabled. Default `20.0`. Must be > 0. |
| `--plot / --no-plot` | Write `htc_report.html` after the run. Default `--plot`. |
| `--detailed-output-heating-cooling` | Request more detailed engine output for heating/cooling. Off by default. |

## `h3 calibrate`

### Core inputs

| Flag | Meaning |
|---|---|
| `--input PATH` | Base model JSON to calibrate. |
| `--weather PATH` | EPW weather file. |
| `--external-conditions PATH` | `ExternalConditions` JSON input. |
| `--measured-htc PATH` | Required measured HTC JSON target. |
| `--out-dir PATH` | Override the output base directory. |
| `--label TEXT` | Prefix for the calibration folder. |
| `--offset FLOAT` | Temperature offsets in K. Repeatable. |
| `--out PATH` | Explicit calibrated summary output path. |

### Calibration controls

| Flag | Meaning |
|---|---|
| `--delta-scalar FLOAT` | Probe size used to estimate sensitivity. Default `0.2`. |
| `--lambda-reg FLOAT` | Regularisation weight for the scalar fit. Default `0.01`. |
| `--scalar-min FLOAT` | Lower bound for the shared scalar. Default `0.1`. |
| `--scalar-max FLOAT` | Upper bound for the shared scalar. Default `2.5`. |
| `--opt-steps INTEGER` | Number of iterative refinement steps after the initial solve. Default `5`. |
| `--opt-tol-w-per-k FLOAT` | Early-stop tolerance for iterative refinement. Default `0.5`. |
| `--calibration-stat [mean|median]` | Statistic used for the masked monthly calibration target. Default `mean`. |
| `--mask-block-hours FLOAT` | Block-gating granularity in hours. Default `24.0`; use `0` to disable block gating. |

### Masking and filtering

| Flag | Meaning |
|---|---|
| `--heating-eps-w FLOAT` | Heating-active threshold, in W. Default `0.0`. |
| `--unmet-eps-kwh FLOAT` | Unmet-demand threshold, in kWh per timestep. Default `0.0`. |
| `--valid-min-fraction FLOAT` | Minimum valid fraction within a block. Default `0.95`. |
| `--window start_idx,end_idx` | Manual index window. Default: the full run. |
| `--heating-only` | Restrict analysis to heating timesteps only. Off by default. |

### Engine and output controls

| Flag | Meaning |
|---|---|
| `--hem-bin PATH` | Path to the Rust HEM binary. |
| `--fhs-assumptions / --no-fhs-assumptions` | Run via the patched FHS wrapper binary instead of the core engine. Default `--fhs-assumptions`. |
| `--disable-fhs-heating-schedule / --no-disable-fhs-heating-schedule` | Disable the patched FHS heating schedule. Default enabled. |
| `--extra-flag TEXT` | Additional raw flags forwarded to the HEM engine. Repeatable. |
| `--fee-metrics / --no-fee-metrics` | Run lightweight FEE post-processing for the baseline and final calibrated models only. Default `--fee-metrics`. |
| `--fixed-electric-heating / --no-fixed-electric-heating` | Replace **every** zone's heating system with a fixed-size electric heater. Default **enabled**. |
| `--fixed-electric-heater-kw-per-zone FLOAT` | Rated heater size per zone. Default `20.0`. |
| `--dry` | Analyse existing outputs without re-running simulations. Off by default. |
| `--plot / --no-plot` | Write calibrated and baseline HTML reports. Default `--plot`. |
| `--require-patched / --no-require-patched` | Require a verified patched binary. Default `--require-patched`. |
| `--detailed-output-heating-cooling` | Request more detailed engine output for heating/cooling. Off by default. |

## `h3 plot`

| Flag | Meaning |
|---|---|
| `--no-recursive` | Do not recurse into subdirectories when plotting from a directory path. |
| `--html PATH` | Write a single HTML report to a chosen output path instead of opening the browser. |
| `--heating-eps-w FLOAT` | Heating-active threshold used for masking in the plot. Default `0.0`. |
| `--unmet-eps-kwh FLOAT` | Unmet-demand threshold used for masking in the plot. Default `0.0`. |
| `--valid-min-fraction FLOAT` | Block filter threshold used in the plot. Default `0.95`. |
| `--agg-hours FLOAT` | Aggregation bins to show in the plot. Repeatable; defaults to the summary's own bins. |

## `h3 setup`

| Flag | Meaning |
|---|---|
| `--reconfigure` | Skip venv/dependency/build steps and jump straight to path configuration. |

## `h3 config`

| Flag | Meaning |
|---|---|
| `--write` | Persist the updated values back to the config file. |
| `--hem-bin PATH` | Save a new Rust HEM binary path. |
| `--input-dir PATH` | Save the input directory. |
| `--weather-dir PATH` | Save the weather directory. |
| `--external-conditions-dir PATH` | Save the external-conditions directory. |
| `--output-dir PATH` | Save the output directory. |
| `--out PATH` | Write the resulting JSON to a chosen path instead of stdout. |

## `h3 doctor`

| Flag | Meaning |
|---|---|
| `--out PATH` | Write the diagnostic JSON to a file instead of stdout. |

## `h3 build-hem`

| Flag | Meaning |
|---|---|
| `--clean / --no-clean` | Rebuild from scratch or reuse an existing matching build. |
| `--write-config / --no-write-config` | Save the built binary path into the config file. |

An installed wheel stores cloned source and Cargo output below
`$XDG_CACHE_HOME/htc-harness` when that variable is set, or
`~/.cache/htc-harness` otherwise. The `HTC_HARNESS_BUILD_ROOT` environment
variable overrides that location.
