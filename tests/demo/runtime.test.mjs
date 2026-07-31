// SPDX-License-Identifier: AGPL-3.0-only
// SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
// Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
//
// End-to-end test of the browser demo runtime against a REAL Pyodide.
//
// The engine is mocked by replaying recorded HEM output (tests/fixtures), so
// everything else is real: the h3 wheel, the bridge, Pyodide's filesystem, and
// h3's own CSV reader. Only the Rust WASM itself is stood in for.
//
//   node --test tests/demo/runtime.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadPyodide } from "pyodide";
import * as runtime from "../../demo/h3-runtime.js";
import {
  parseHemInvocation,
  installH3,
  runSandboxDesign,
  runSandboxEmpirical,
  verifyWheelIntegrity,
} from "../../demo/h3-runtime.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "../..");
const FIXTURES = path.join(ROOT, "tests/fixtures");

test("browser runtime exposes only the fixed sandbox", () => {
  assert.equal("runDemo" in runtime, false);
});

test("wheel integrity check rejects missing or mismatched hashes", async () => {
  const wheelBytes = new TextEncoder().encode("h3");
  const expected = "97fb5f8538b89f6c1accfd19836b65a73b61fbc2e0cbf84bb858a0fffa3f1592";

  await assert.doesNotReject(verifyWheelIntegrity(wheelBytes, expected));
  await assert.rejects(
    verifyWheelIntegrity(wheelBytes, "0".repeat(64)),
    /wheel integrity check failed/i,
  );
  await assert.rejects(
    verifyWheelIntegrity(wheelBytes, ""),
    /valid wheel_sha256/i,
  );
});

function recordedEngineOutput() {
  const dir = path.join(FIXTURES, "hem_output");
  return Object.fromEntries(
    fs.readdirSync(dir).map((f) => [f, fs.readFileSync(path.join(dir, f), "utf8")]),
  );
}

/**
 * What the FHS wrapper returns for `--preprocess-only`: the model it would have
 * simulated, keyed `<calculation key>__preproc.json`.
 *
 * Trimmed to what h3 acts on. The heating pattern has to be here, with the
 * setbacks and the `advanced_start` the real wrapper writes, or the test would
 * pass without exercising the flattening at all.
 */
function recordedPreprocessOutput(modelJson) {
  const setpoint = 20.18;
  const model = JSON.parse(modelJson);
  return {
    "FHS__preproc.json": JSON.stringify({
      ...model,
      SimulationTime: { start: 0, end: 48, step: 0.5 },
      SpaceHeatSystem: {
        "Zone 1 circuit": {
          type: "WetDistribution",
          Control: "HeatingPattern_Zone 1 circuit",
        },
      },
      Control: {
        "HeatingPattern_Zone 1 circuit": {
          type: "SetpointTimeControl",
          start_day: 0,
          time_series_step: 0.5,
          advanced_start: 2,
          schedule: {
            main: [{ value: "week", repeat: 53 }],
            week: [
              { value: "weekday", repeat: 5 },
              { value: "weekend", repeat: 2 },
            ],
            weekday: Array(14)
              .fill(null)
              .concat(Array(5).fill(setpoint), Array(29).fill(null)),
            weekend: Array(17)
              .fill(null)
              .concat(Array(10).fill(setpoint), Array(21).fill(null)),
          },
        },
      },
    }),
  };
}

function findWheel() {
  const dist = path.join(ROOT, "dist");
  if (!fs.existsSync(dist)) return null;
  const w = fs.readdirSync(dist).find((f) => f.endsWith(".whl"));
  return w ? path.join(dist, w) : null;
}

test("parseHemInvocation derives the results directory h3 expects", () => {
  const parsed = parseHemInvocation([
    "/hem/hem",
    "/h3/out/demo_base/model.json",
    "-e",
    "/h3/out/demo_base/weather.epw",
    "--heat-balance",
  ]);
  assert.equal(parsed.inputPath, "/h3/out/demo_base/model.json");
  assert.equal(parsed.weatherPath, "/h3/out/demo_base/weather.epw");
  assert.equal(parsed.resultsDir, "/h3/out/demo_base/model__results");
  assert.equal(parsed.heatBalance, true);
});

test("parseHemInvocation handles external-conditions runs (no -e)", () => {
  const parsed = parseHemInvocation([
    "/hem/hem",
    "/h3/out/demo_base/model.json",
    "--heat-balance",
  ]);
  assert.equal(parsed.weatherPath, null);
  assert.equal(parsed.resultsDir, "/h3/out/demo_base/model__results");
});

// h3 signals FHS assumptions with `--mode actual`, which is how the bridge
// knows to ask the WASM export for the wrapper rather than the core engine.
// Missing this would silently run core, which gives a different HTC.
test("parseHemInvocation detects FHS assumptions from --mode actual", () => {
  const fhs = parseHemInvocation([
    "/hem/future-homes-standard",
    "/h3/out/demo_base/model.json",
    "-e",
    "/h3/out/demo_base/weather.epw",
    "--heat-balance",
    "--mode",
    "actual",
  ]);
  assert.equal(fhs.fhsAssumptions, true);

  const core = parseHemInvocation([
    "/hem/hem",
    "/h3/out/demo_base/model.json",
    "--heat-balance",
  ]);
  assert.equal(core.fhsAssumptions, false);

  // `--mode` with a different value is not FHS assumptions.
  const other = parseHemInvocation([
    "/hem/hem",
    "/h3/out/demo_base/model.json",
    "--mode",
    "notional",
  ]);
  assert.equal(other.fhsAssumptions, false);
});

test("parseHemInvocation carries --preprocess-only through to the engine", () => {
  // How the demo disables the FHS heating schedule. If this flag were dropped
  // the engine would simulate instead of returning its preprocessed model, and
  // h3 would run the standard schedule — the exact difference from a CLI run
  // this route exists to remove.
  const preprocess = parseHemInvocation([
    "/hem/future-homes-standard",
    "/h3/out/demo_base/model.json",
    "-e",
    "/h3/out/demo_base/weather.epw",
    "--heat-balance",
    "--mode",
    "actual",
    "--preprocess-only",
  ]);
  assert.equal(preprocess.preprocessOnly, true);
  assert.equal(preprocess.fhsAssumptions, true);
  // The preprocessed model must land where h3 looks for it.
  assert.equal(preprocess.resultsDir, "/h3/out/demo_base/model__results");

  // The core run that follows carries neither flag.
  const core = parseHemInvocation([
    "/hem/hem",
    "/h3/out/demo_base/model__preproc_flat.json",
    "--heat-balance",
  ]);
  assert.equal(core.preprocessOnly, false);
  assert.equal(core.fhsAssumptions, false);
  assert.equal(
    core.resultsDir,
    "/h3/out/demo_base/model__preproc_flat__results",
  );
});

/**
 * The sandbox entry points, which are what the /h3 page actually calls.
 *
 * Same replayed engine as above. What is being tested is that the levers reach
 * the model, that the run uses the shortened period, and that the payload the
 * page destructures is the payload Python produces — the three seams where the
 * page and the wheel could disagree without either being obviously wrong.
 */
test("the sandbox design run drives the engine once and returns an HTC", async (t) => {
  const wheel = findWheel();
  if (!wheel) {
    t.skip("no wheel in dist/ — run: python -m build --wheel --outdir dist .");
    return;
  }

  const pyodide = await loadPyodide();
  await installH3(pyodide, fs.readFileSync(wheel));

  const invocations = [];
  let leveredModel = null;
  const runHem = ({ modelJson, fhsAssumptions, preprocessOnly }) => {
    invocations.push({ fhsAssumptions, preprocessOnly });
    if (preprocessOnly) {
      leveredModel = JSON.parse(modelJson);
      return recordedPreprocessOutput(modelJson);
    }
    return recordedEngineOutput();
  };

  const result = await runSandboxDesign({
    pyodide,
    runHem,
    values: { wallU: 0.9, floorArea: 142.4 },
  });

  // One offset, so one wrapper run and one core run — not the three an
  // empirical estimate needs. Design HTC is read off a single run's static
  // output and would be identical across offsets anyway.
  assert.deepEqual(
    invocations.map((i) => [i.fhsAssumptions, i.preprocessOnly]),
    [
      [true, true],
      [false, false],
    ],
  );

  // The levers must have reached the model the engine saw, or the sliders move
  // a picture and nothing else.
  //
  // The control asks for 142.4 m2 of *total* floor area, which on this
  // two-storey dwelling is a 71.2 m2 footprint — twice the authored 35.6. Were
  // the request passed through as the footprint, the house would come out twice
  // as big again.
  assert.equal(leveredModel.GroundFloorArea, 71.2);
  const elements = leveredModel.Zone["Zone 1"].BuildingElement;
  const walls = Object.values(elements).filter(
    (e) => e.type === "BuildingElementOpaque" && e.pitch === 90 && !e.is_external_door,
  );
  assert.ok(walls.length > 0, "fixture model has no external walls");
  for (const wall of walls) assert.equal(wall.u_value, 0.9);

  assert.equal(typeof result.htcWPerK, "number");
  // HLP is quoted per square metre of total floor area, which is what the
  // control asked for.
  assert.equal(result.floorAreaM2, 142.4);
  assert.ok(
    Math.abs(result.hlpWPerM2K - result.htcWPerK / 142.4) < 1e-9,
    "HLP must be HTC per square metre of total floor area",
  );
});

test("the sandbox empirical run shortens the period and returns a weekly series", async (t) => {
  const wheel = findWheel();
  if (!wheel) {
    t.skip("no wheel in dist/ — run: python -m build --wheel --outdir dist .");
    return;
  }

  const pyodide = await loadPyodide();
  await installH3(pyodide, fs.readFileSync(wheel));

  const invocations = [];
  const coreModels = [];
  const runHem = ({ modelJson, preprocessOnly, fhsAssumptions }) => {
    invocations.push({ fhsAssumptions, preprocessOnly });
    if (preprocessOnly) return recordedPreprocessOutput(modelJson);
    coreModels.push(JSON.parse(modelJson));
    return recordedEngineOutput();
  };

  const events = [];
  const result = await runSandboxEmpirical({
    pyodide,
    runHem,
    values: {},
    onProgress: (event) => events.push(event),
  });

  // Base plus two offsets, each a wrapper run then a core run.
  assert.equal(invocations.length, 6);

  // Jan-Mar, applied to the model the core engine ran. The wrapper writes its
  // own SimulationTime while resolving the weather year, so this is the only
  // place the override can survive.
  for (const model of coreModels) {
    assert.deepEqual(model.SimulationTime, { start: 0, end: 2160, step: 0.5 });
  }

  assert.equal(typeof result.designHtcWPerK, "number");
  assert.ok(Array.isArray(result.weeklyHtcWPerK), "no weekly series returned");
  assert.deepEqual(result.periodHours, [0, 2160, 0.5]);
  // Progress must have reached the host, or the page shows a dead bar for the
  // whole of the slowest thing it does.
  assert.deepEqual(
    events.map(({ type, label, index, total }) => ({
      type,
      label,
      ...(index === undefined ? {} : { index }),
      total,
    })),
    [
      { type: "batch_start", label: "empirical", total: 3 },
      { type: "run_start", label: "empirical_base", index: 1, total: 3 },
      { type: "run_complete", label: "empirical_base", index: 1, total: 3 },
      { type: "run_start", label: "empirical_minus1K", index: 2, total: 3 },
      { type: "run_complete", label: "empirical_minus1K", index: 2, total: 3 },
      { type: "run_start", label: "empirical_plus1K", index: 3, total: 3 },
      { type: "run_complete", label: "empirical_plus1K", index: 3, total: 3 },
    ],
  );
});

test("an unknown attribute is refused rather than ignored", async (t) => {
  const wheel = findWheel();
  if (!wheel) {
    t.skip("no wheel in dist/ — run: python -m build --wheel --outdir dist .");
    return;
  }

  const pyodide = await loadPyodide();
  await installH3(pyodide, fs.readFileSync(wheel));

  await assert.rejects(
    runSandboxDesign({
      pyodide,
      runHem: () => ({}),
      values: { wallUValue: 0.9 },
    }),
    /Unknown sandbox attribute/,
    "a renamed slider must fail loudly, not silently stop working",
  );
});
