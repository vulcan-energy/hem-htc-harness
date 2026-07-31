# CLI Usage

The `h3` CLI is organised around a small number of commands. Most users will
mainly use `setup`, `run`, `calibrate`, `plot`, and `doctor`.

## Command overview

Top-level commands:

- `h3 setup`: create the repo venv, install dependencies, build patched HEM,
  and configure paths.
- `h3 run`: run a base-plus-offset workflow and write `htc_summary.json`.
- `h3 calibrate`: fit a shared scalar against a measured HTC target.
- `h3 plot`: turn one or more `htc_summary*.json` files into an HTML report.
- `h3 config`: inspect or update the saved per-user config.
- `h3 doctor`: print configuration and prerequisite diagnostics.
- `h3 build-hem`: build the patched HEM binary from `patches/manifest.json`.

## `h3 setup`

Use this first in a fresh checkout:

```bash
h3 setup
```

What it does:

1. checks for Python, Git, Cargo, and Rust,
2. creates `.venv` with Python 3.12+,
3. installs the editable package plus plotting dependencies,
4. builds the patched HEM binary,
5. prompts for your input, weather, external-conditions, and output paths,
6. runs `h3 doctor`.

Useful variant:

```bash
h3 setup --reconfigure
```

That skips the environment/build steps and just re-prompts for paths.

## `h3 run`

This is the main analysis command.

Basic EPW example:

```bash
h3 run --input model.json --weather weather.epw
```

Basic `ExternalConditions` example:

```bash
h3 run --input model.json --external-conditions external_conditions.json
```

Analyse an existing output directory without re-running HEM:

```bash
h3 run --dry --input model.json --weather weather.epw
```

Common things to know:

- offsets default to `-1.0 K` and `+1.0 K`,
- H3 inserts the base case automatically if `0.0` is not supplied,
- by default it writes `htc_summary.json` and then generates
  `htc_report.html`,
- it runs the pinned Rust engine and FHS wrapper built by `h3 setup`.

Two defaults change the model before it runs, and are worth knowing about:

- `--fixed-electric-heating` (on) replaces every zone's heating system with a
  20 kW `InstantElecHeater`, so the offset runs always have a heat source that
  can meet demand,
- `--fhs-assumptions` (on) runs the patched FHS wrapper binary rather than the
  core engine.

Pass `--no-fixed-electric-heating` or `--no-fhs-assumptions` to run the model as
written. See [Flags Reference](flags.md#h3-run).

## `h3 calibrate`

Use this when you have a measured HTC target and want H3 to fit a shared
scalar.

Example:

```bash
h3 calibrate \
  --input model.json \
  --external-conditions ec.json \
  --measured-htc measured.json
```

Important behaviour:

- calibration requires the patched Rust HEM/FHS build,
- a measured HTC file is required,
- baseline artifacts are written alongside the calibrated outputs,
- iterative refinement is enabled by default.

## `h3 plot`

This command plots one or more summary files.

Examples:

```bash
h3 plot output/
```

```bash
h3 plot output/ --html report.html
```

Use it when you already have `htc_summary.json` and want to regenerate or
combine reports.

## `h3 config`

This command reads or writes the per-user config file.

Show the current config:

```bash
h3 config
```

Write new paths:

```bash
h3 config --write --input-dir /path/to/inputs --output-dir /path/to/output
```

## `h3 doctor`

This prints diagnostic information as JSON, including:

- prerequisite tool availability,
- current config path,
- the configured HEM binary,
- basic executable/help checks.

Run it any time you want a quick health check:

```bash
h3 doctor
```

## `h3 build-hem`

This builds the pinned, patched Rust HEM engine and FHS wrapper defined by
`patches/manifest.json`. Source checkouts keep the build under
`.patch-workdir/`. Installed wheels carry the same manifest and patch files and
build under `$XDG_CACHE_HOME/htc-harness/.patch-workdir/` when that variable is
set, or `~/.cache/htc-harness/.patch-workdir/` otherwise, so the installed
package itself remains read-only. Set `HTC_HARNESS_BUILD_ROOT` to use a
different writable build location.

Typical usage:

```bash
h3 build-hem
```

Rebuild from scratch:

```bash
h3 build-hem --clean
```

## Path resolution

For user-supplied file paths, H3 resolves them in this order:

1. absolute paths are used as-is,
2. if the relevant configured base directory exists, the path is resolved
   inside that directory,
3. if the relative path exists from the current working directory, it is used,
4. otherwise H3 passes the path through and validation fails later with a clear
   error.

That lets you use short relative paths once `h3 setup` or `h3 config --write`
has been used.
