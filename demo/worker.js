// SPDX-License-Identifier: AGPL-3.0-only
// SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
// Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
//
// h3 in-browser sandbox worker: a thin message-passing shell over h3-runtime.js.
// The logic lives there so it can be tested under Node against a real Pyodide
// (tests/demo/runtime.test.mjs); this file only wires it to the host page.
//
// It starts loading as soon as it is constructed rather than waiting for an
// "init" message. The host has already decided — the worker is only created
// after someone presses "Try the demo" — and a round trip before the 30 MB
// download begins buys nothing.
//
// Host -> worker:
//   { id, type: "design",    payload: <attributes> }
//   { id, type: "empirical", payload: <attributes> }
//
// Worker -> host:
//   { type: "progress", payload: { stage, fraction, label } }
//   { type: "progress", payload: { type: "batch_start" | "run_start" |
//       "run_complete", label, index?, total, offset_k?, elapsed_s? } }
//   { type: "ready" }
//   { id, type: "result", payload } | { id, type: "error", error }
//
// The contract is stated on the other side in
// apps/web/app/(marketing)/h3/h3Engine.ts.

import {
  installH3,
  runSandboxDesign,
  runSandboxEmpirical,
  verifyWheelIntegrity,
} from "./h3-runtime.js";

// Pyodide is served from jsDelivr, which the site's CSP already allows. Pinned
// exactly: `micropip.install` resolves click from the runtime's own package set,
// and "latest" would eventually resolve it against a Python h3 has not been
// tested on.
const PYODIDE_VERSION = "314.0.3";
const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

// The engine is NOT copied into the demo bundle. It is the same artifact the
// rest of the site loads, at a stable same-origin path, refreshed by the site's
// own release pipeline. A second copy would be a second thing to keep in step,
// and the one that went stale would still run and still report numbers.
// Absolute, because it is also used as the base for resolving the .wasm
// beside it, and `new URL(name, base)` rejects a root-relative base.
const ENGINE_URL = new URL("/launch/wasm_wrapper.js", self.location.origin).href;

// Everything h3 publishes sits beside this file.
const BUNDLE_BASE = new URL("./", import.meta.url);

const post = (message) => self.postMessage(message);

const progress = (stage, fraction, label) =>
  post({ type: "progress", payload: { stage, fraction, label } });

let pyodide = null;
let engine = null;

/**
 * Fetch with byte-level progress.
 *
 * The engine is ~20 MB and dominates the wait. Reporting it properly is the
 * difference between a bar that moves and a spinner that asks for trust, and on
 * a slow connection that is what decides whether someone waits.
 *
 * Falls back to an indeterminate-but-honest single step if the response carries
 * no Content-Length, which is what a chunked or compressed response looks like.
 */
async function fetchWithProgress(url, onFraction) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${url} responded ${response.status}`);
  }

  const total = Number(response.headers.get("content-length")) || 0;
  if (!total || !response.body) {
    return await response.arrayBuffer();
  }

  const reader = response.body.getReader();
  const chunks = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    onFraction(Math.min(1, received / total));
  }

  const buffer = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    buffer.set(chunk, offset);
    offset += chunk.length;
  }
  return buffer.buffer;
}

/**
 * Assert the engine the site is serving is one this bundle can drive.
 *
 * The manifest records the export and its arity because the two sides are
 * released separately: h3 publishes the bundle, the site publishes the engine.
 * A build from a different pin would otherwise fail deep inside a run, as a
 * missing CSV column, long after the cause.
 */
function assertEngineMatches(manifest, module) {
  const name = manifest?.requires_engine_export;
  if (!name) return;

  const exported = module[name];
  if (typeof exported !== "function") {
    throw new Error(
      `The Home Energy Model build at ${ENGINE_URL} does not export ${name}. ` +
        `It is older than this demo bundle and cannot run it.`,
    );
  }

  const arity = manifest.engine_export_arity;
  if (typeof arity === "number" && exported.length !== arity) {
    throw new Error(
      `${name} at ${ENGINE_URL} takes ${exported.length} parameters, ` +
        `but this demo bundle needs ${arity}. The engine and the bundle are ` +
        `from different releases.`,
    );
  }
}

async function init() {
  progress("python", 0.05, "Starting Python");

  const manifest = await (
    await fetch(new URL("h3-demo.json", BUNDLE_BASE))
  ).json();

  const { loadPyodide } = await import(`${PYODIDE_URL}pyodide.mjs`);
  pyodide = await loadPyodide({ indexURL: PYODIDE_URL });
  progress("python", 0.2, "Python ready");

  // The engine, fetched by hand so the download can be reported. wasm-bindgen
  // would otherwise fetch it itself, silently, from inside module.default().
  progress("engine", 0.2, "Downloading the Home Energy Model");
  const module = await import(ENGINE_URL);
  const wasmBytes = await fetchWithProgress(
    new URL(module.WASM_FILENAME ?? "wasm_wrapper_bg.wasm", ENGINE_URL).href,
    (fraction) =>
      progress(
        "engine",
        0.2 + fraction * 0.6,
        "Downloading the Home Energy Model",
      ),
  );
  await module.default({ module_or_path: wasmBytes });
  assertEngineMatches(manifest, module);
  engine = module;
  progress("engine", 0.82, "Engine ready");

  progress("h3", 0.85, "Installing h3");
  const wheel = await (
    await fetch(new URL(manifest.wheel, BUNDLE_BASE))
  ).arrayBuffer();
  await verifyWheelIntegrity(wheel, manifest.wheel_sha256);
  await installH3(pyodide, wheel, manifest.wheel);

  // The dwelling and its weather ride inside the wheel, so by this point they
  // are already on the filesystem. Importing proves it before the page offers
  // a control that would fail on first use.
  progress("model", 0.95, "Preparing the dwelling");
  await pyodide.runPythonAsync(
    "from htc_harness.sandbox import load_sandbox_model, load_sandbox_weather\n"
      + "assert load_sandbox_model() and load_sandbox_weather()\n",
  );

  post({ type: "ready" });
}

function runHem({
  modelJson,
  weatherEpw,
  heatBalance,
  fhsAssumptions,
  preprocessOnly,
}) {
  // A build predating either extra parameter ignores it. Missing
  // fhs_assumptions means the build runs core; missing preprocess_only means
  // it simulates rather than returning the preprocessed model, and h3 then
  // reports that no `__preproc.json` was written rather than silently running
  // the standard heating schedule.
  const raw = engine.run_hem_for_h3(
    modelJson,
    weatherEpw ?? "",
    heatBalance,
    Boolean(fhsAssumptions),
    Boolean(preprocessOnly),
  );
  const result = JSON.parse(raw);
  // The export signals failure as { error: "..." }. Without this check the
  // bridge would happily write a file literally named "error" and h3 would
  // then fail much later with a confusing missing-column message.
  if (result && typeof result.error === "string") {
    throw new Error(`HEM engine failed: ${result.error}`);
  }
  return result;
}

// One run at a time. Pyodide is single-threaded and the runs share a virtual
// filesystem, so overlapping them would interleave one run's output into
// another's results directory.
let queue = Promise.resolve();

function enqueue(work) {
  const result = queue.then(work, work);
  queue = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

// Dragging a slider fires a design request per step, and each run takes about a
// second. Running them all would mean the figure on screen kept reporting
// positions the slider left long ago, for as long as the backlog took to drain.
//
// So intermediate positions are dropped rather than queued: one run is in
// flight, and when it finishes the newest position is run next. Every caller
// still gets a result — the result for where the slider actually ended up,
// which is the number the page wants to display anyway.
let latestValues = null;
let inflightDesign = null;

function design(values) {
  latestValues = values;
  if (inflightDesign) return inflightDesign;

  inflightDesign = enqueue(async () => {
    let result;
    while (latestValues !== null) {
      const target = latestValues;
      // Cleared before the run, so a position arriving mid-run is seen as new.
      latestValues = null;
      result = await runSandboxDesign({ pyodide, runHem, values: target });
    }
    // No await between the failed loop test and this, so no request can slip
    // in and be dropped by the promise it would otherwise have joined.
    inflightDesign = null;
    return result;
  }).catch((error) => {
    inflightDesign = null;
    latestValues = null;
    throw error;
  });

  return inflightDesign;
}

function empirical(values) {
  return enqueue(() =>
    runSandboxEmpirical({
      pyodide,
      runHem,
      values,
      onProgress: (event) => post({ type: "progress", payload: event }),
    }),
  );
}

const ready = init().catch((error) => {
  // Surfaced as a worker error so the host's `onerror` reports the sandbox
  // unavailable rather than every later call timing out.
  post({
    type: "error",
    error: `The sandbox failed to start: ${error?.message ?? error}`,
  });
  throw error;
});

self.onmessage = async ({ data: message }) => {
  const { id, type, payload } = message ?? {};
  try {
    await ready;
    if (type === "design") {
      post({ id, type: "result", payload: await design(payload) });
      return;
    }
    if (type === "empirical") {
      post({ id, type: "result", payload: await empirical(payload) });
      return;
    }
    throw new Error(`unknown request type: ${type}`);
  } catch (error) {
    post({ id, type: "error", error: String(error?.message ?? error) });
  }
};
