# HEM HTC HARNESS (h3)

[![Licence: AGPL v3](https://img.shields.io/badge/licence-AGPL--3.0--only-blue.svg)](LICENSE)

`h3` is a Python CLI for computing empirical HTC (heat transfer coefficient,
W/K) from HEM outputs and comparing the modelled result with a measured target.

Part of **Vulcan Community**, originally developed by Home Energy Foundry
Limited. Licensed under [AGPL-3.0-only](LICENSE), supplemented by the
[Vulcan Origin Terms](ADDITIONAL_TERMS.md). Commercial terms are available —
contact `info@usevulcan.app`.

`h3` is an independent tool that reads Home Energy Model output. It is not
produced, endorsed or certified by MHCLG or by any HEM governance body.

h3 has two supported surfaces: this CLI, which drives patched builds of the
public Rust HEM engine and FHS wrapper locally for real projects, and a fixed
synthetic browser sandbox that explains the method. The sandbox accepts no
project uploads; real projects always run locally through the CLI.

## Documentation

User-facing documentation lives in `docs/`. The public home for h3 is
`https://usevulcan.app/h3`.

- Docs home: `docs/index.md`
- Methodology: `docs/methodology.md`
- CLI usage: `docs/cli.md`
- Flags reference: `docs/flags.md`
- Outputs reference: `docs/outputs.md`
- Browser demo: `docs/demo.md`
- Codebase guide: `docs/codebase.md`

## Quickstart

**Prerequisites:** Python 3.12+, Rust/Cargo, and Git.

Bootstrap a source checkout, then let h3 build its pinned Rust engine and FHS
wrapper:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[plot]"
h3 setup
```

`h3 setup` then:

1. checks prerequisites,
2. reuses `.venv`, creating it and installing dependencies only if needed,
3. builds the patched HEM and FHS binaries,
4. prompts for paths,
5. runs `h3 doctor`.

You do **not** need a local HEM repo checkout. `h3 setup` clones the pinned
commit automatically from `patches/manifest.json`.

Useful follow-ups:

- Reconfigure paths only: `h3 setup --reconfigure`
- Check your setup: `h3 doctor`

## Common commands

Run a simulation and analysis:

```bash
h3 run --input model.json --weather weather.epw
```

Use `ExternalConditions` instead of EPW:

```bash
h3 run --input model.json --external-conditions external_conditions.json
```

Analyse an existing run without re-running HEM:

```bash
h3 run --dry --input model.json --weather weather.epw
```

Calibrate against a measured HTC target:

```bash
h3 calibrate \
  --input model.json \
  --external-conditions ec.json \
  --measured-htc measured.json
```

Plot an existing summary:

```bash
h3 plot /path/to/htc_summary.json
```

For the full command surface, use the docs site or:

```bash
python -m htc_harness.cli --help
python -m htc_harness.cli run --help
python -m htc_harness.cli calibrate --help
```

## Local docs preview

Preview the docs site locally:

```bash
. .venv/bin/activate
pip install -e ".[docs]"
mkdocs serve
```

Then open `http://127.0.0.1:8000`.

Build the static site:

```bash
mkdocs build --strict
```

## Development

Manual install without `h3 setup`:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[plot]"
h3 build-hem
```

The release wheel also includes the pinned build manifest and patches, so its
`h3 build-hem` command works without a source checkout. Wheel installs build in
the user cache rather than inside `site-packages`; set
`HTC_HARNESS_BUILD_ROOT` to override that location.

Useful extras:

- Dev tools: `pip install -e ".[dev]"`
- Docs tools: `pip install -e ".[docs]"`

Run tests:

```bash
pytest
```

Run the real HEM integration test:

```bash
HTC_HARNESS_RUNNER_INTEGRATION=1 HTC_HARNESS_HEM_BIN=/path/to/hem pytest
```

## Repo structure

`h3` is a library with two front ends. The run pipeline imports no `click` and
starts no subprocess of its own, which is what lets the same code run in a
browser under Pyodide. See `docs/codebase.md`.

| Path | Purpose |
|---|---|
| `src/htc_harness/api.py` | Library entry point; owns every run default |
| `src/htc_harness/cli.py` | `click` front end, plus `setup` / `doctor` / `build-hem` |
| `src/htc_harness/browser.py` | Pyodide front end for the in-browser demo |
| `src/htc_harness/workflow.py` | UI-free run orchestration, shared by `run` and `calibrate` |
| `src/htc_harness/paths.py` | Path resolution and run-directory staging |
| `src/htc_harness/runner.py` | HEM command builder and the injectable executor seam |
| `src/htc_harness/reader.py` | CSV discovery, parsing, and model transforms |
| `src/htc_harness/empirical.py` | Core HTC calculations |
| `src/htc_harness/calibration.py` | Calibration solve and iterative refinement |
| `src/htc_harness/plotting/` | Plot loading, figures, and HTML reports |
| `src/htc_harness/config.py` | Per-user config defaults |
| `demo/` | Browser demo runtime and worker |
| `patches/` | Rust HEM/FHS patches and pinned public-source manifest |
| `tests/` | Unit tests, plus a Pyodide runtime test under `tests/demo/` |

## Deployment

The repo includes:

- `mkdocs.yml` for site structure,
- `vercel.json` for static-site deployment on Vercel.

For Vercel, point the project at the repo root. The build installs the docs
extra and runs `mkdocs build --strict`, producing the static site in `site/`.

## Contributing

Contributions are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md).

Issues, bug reports and methodology challenges need no paperwork. Code and
substantive documentation need a contributor agreement
([individual](CLA-INDIVIDUAL.md) / [entity](CLA-ENTITY.md)) so that Home Energy
Foundry Limited can continue to offer h3 under both AGPL and commercial terms.
You keep ownership of your contribution, and it must always remain available
under the licence you submitted it to.

We are especially interested in methodology challenges and in validation against
real measured HTC. Please do not attach identifiable building data to issues or
pull requests.

- Code of conduct: [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
- Security: [`SECURITY.md`](SECURITY.md) — report privately, never as an issue

`main` is protected; all changes land via pull request and must pass CI,
including the licensing and attribution gate:

```bash
python scripts/check_licensing.py
```

## Licence

`h3` is licensed under [AGPL-3.0-only](LICENSE), supplemented by the
[Vulcan Origin Terms](ADDITIONAL_TERMS.md) under AGPL sections 7(b), 7(c) and
7(e). See [`NOTICE`](NOTICE) for attribution and third-party material, and
[`TRADEMARKS.md`](TRADEMARKS.md) for the brand policy.

The Home Energy Model engine and Future Homes Standard wrapper are Crown
Copyright and remain under their canonical MIT licences; see
[`LICENSES/`](LICENSES). The patches in [`patches/`](patches) are AGPL-covered
Vulcan material and are default-inert — with no h3 environment variables set,
a patched build produces results identical to an unpatched build.
