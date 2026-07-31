# Browser sandbox

H3 includes a fixed browser sandbox for exploring how dwelling form, orientation
and seven numeric building attributes affect design and empirical HTC. It runs
entirely in the browser using Pyodide, the h3 wheel and the site's HEM
WebAssembly build.

The sandbox model is synthetic, ships inside the wheel and carries no measured
or client data. The browser surface deliberately does **not** accept arbitrary
HEM models, weather files or measured-HTC uploads. Use the local CLI for real
projects.

## Supported controls

- dwelling form: detached, end terrace, mid terrace or flat
- flat floor: ground, middle or top
- flat aspect: single or dual
- front orientation in 45-degree increments
- total floor area
- wall U-value
- roof U-value
- glazing U-value
- glazing area
- airtightness
- thermal bridging

The design action runs HEM once and reports design HTC and HLP. The empirical
action runs the normal base and temperature-offset analysis and returns the
weekly HTC series. Both paths use the same library defaults and model transforms
as the CLI, with the browser-specific FHS preprocessing route required by WASM.

The browser does not support calibration, arbitrary engine flags or generated
Plotly reports. Those remain local CLI workflows.

## Runtime boundary

```text
host page -> worker.js -> Pyodide -> htc_harness.browser -> HEM WASM
```

Only the bounded sandbox configuration and numeric control values cross from
the host page to the worker. The bundled model and weather stay in the browser
filesystem.

Engineering details and verification commands are in the repository's
[`demo/README.md`](https://github.com/vulcan-energy/hem-htc-harness/blob/main/demo/README.md).

## Licence

H3 is AGPL-3.0-only with the Vulcan Origin Terms. The HEM engine and bundled
example weather retain their upstream Crown/MIT notices. See `NOTICE`,
`ADDITIONAL_TERMS.md` and `ATTRIBUTION.md`.
