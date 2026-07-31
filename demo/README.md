# h3 browser sandbox

This directory contains the public browser runtime for one fixed synthetic H3
sandbox. The supported product contract is intentionally narrow:

- the host sends one bounded dwelling configuration and seven documented
  numeric building attributes;
- `worker.js` owns loading and message handling;
- `h3-runtime.js` bridges Pyodide to the site's HEM WASM export;
- `htc_harness.browser` applies the attributes to the bundled model and runs
  either design or empirical HTC.

There is no generic upload API. Real models, weather files, measured HTC and
calibration remain local CLI workflows.

## Worker protocol

```text
host -> worker  { id, type: "design"|"empirical", payload: <attributes> }
worker -> host  { type: "progress", payload: ... }
                { type: "ready" }
                { id, type: "result", payload } | { id, type: "error", error }
```

Design requests coalesce so rapid slider changes do not queue stale runs. The
empirical action remains explicit because it performs the base plus two offset
runs.

## Engine contract

The host provides the same-origin `run_hem_for_h3` WASM export:

```text
run_hem_for_h3(model_json, weather_epw, heat_balance, fhs_assumptions,
               preprocess_only) -> { filename: contents }
```

The worker checks the required export and arity recorded in `h3-demo.json`
before running. The browser uses FHS preprocessing, flattens the heating schedule
and then runs the core engine because WASM cannot consume the native environment
variable used by the CLI.

## Assets

The CI demo bundle contains:

- the built h3 wheel;
- `worker.js`;
- `h3-runtime.js`;
- `h3-demo.json`, containing the exact commit, wheel hash and engine contract.

The HEM WASM artifact is supplied separately by the Vulcan release pipeline.
The worker verifies the wheel against `wheel_sha256` before installing it. The
wheel contains the synthetic dwelling and upstream London example weather.

## Verification

The Node test runs a real Pyodide runtime and the real h3 wheel while replaying
recorded HEM CSV output:

```bash
python -m build --wheel --outdir dist .
npm ci
npm run test:browser
```

It verifies package installation, the WASM executor bridge, the fixed design and
empirical entry points, FHS preprocessing, model controls and result payloads.
The Rust WASM engine itself and a real browser remain separate release checks.

## Licence

The h3 runtime is AGPL-3.0-only with the Vulcan Origin Terms. A host must provide
the applicable origin and Corresponding Source notices. The HEM WASM and example
weather retain their Crown/MIT notice; see the repository `NOTICE` and
`LICENSES/` directory.
