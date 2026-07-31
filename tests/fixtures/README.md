# Test fixtures

Synthetic only. Nothing here is real building data.

| Path | Provenance |
|---|---|
| `demo_model.json` | `examples/input/core/short/demo_hp_no_variable_flow.json` from the Home Energy Model engine repository, Crown Copyright, MIT. See `LICENSES/MIT-home-energy-model.txt`. |
| `hem_output/*.csv` | Output of running the patched engine at the commit pinned in `patches/manifest.json` on `demo_model.json` with London EPW weather. |

`hem_output/` is a short demo run — 14 timesteps, not a full year — which is why
it is 32 KB rather than megabytes.

These exist so the browser-demo runtime can be tested end to end without a real
HEM WASM build: `tests/demo/runtime.test.mjs` replays them as a mock engine, so
the test exercises the real bridge, the real Pyodide filesystem handling and the
real h3 reader.

Regenerate after an engine re-pin:

```bash
hem tests/fixtures/demo_model.json -e <london.epw> --heat-balance
cp <model>__results/*.csv tests/fixtures/hem_output/
```
