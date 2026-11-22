// SPDX-License-Identifier: AGPL-3.0-only
// SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
// Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
//
// Testable core of the h3 browser demo.
//
// Kept separate from worker.js so it can be exercised under Node against a real
// Pyodide and a mock engine — see tests/demo/runtime.test.mjs. worker.js is a
// thin message-passing shell over this.

export const H3_WHEEL_NAME = "htc_harness-0.2.0-py3-none-any.whl";

/** Verify the wheel bytes against the SHA-256 recorded by the release job. */
export async function verifyWheelIntegrity(wheelBytes, expectedSha256) {
  if (!/^[0-9a-f]{64}$/i.test(expectedSha256 ?? "")) {
    throw new Error("h3-demo.json did not contain a valid wheel_sha256");
  }
  const bytes =
    wheelBytes instanceof ArrayBuffer
      ? new Uint8Array(wheelBytes)
      : new Uint8Array(wheelBytes.buffer, wheelBytes.byteOffset, wheelBytes.byteLength);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  const actual = Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  if (actual !== expectedSha256.toLowerCase()) {
    throw new Error(
      `h3 wheel integrity check failed: expected ${expectedSha256}, got ${actual}`,
    );
  }
}

/**
 * The engine contract the demo requires.
 *
 * h3 consumes HEM's CSV *output files*, so the WASM export must produce them.
 * It does NOT take argv: the CLI's argument shape is an implementation detail
 * of the native binary, and threading it through WASM buys nothing.
 *
 *   runHem({ modelJson, weatherEpw, headBalance }) -> { [filename]: contents }
 *
 * Returned filenames are written verbatim into the run's `<stem>__results`
 * directory in Pyodide's filesystem, which is exactly where h3 looks for them.
 *
 * Note on environment variables: `std::env::var` is not usable from JS in a
 * wasm32-unknown-unknown build, so h3's HEM_ACH_CAL_FACTOR and
 * HEM_FHS_DISABLE_HEATING_SCHEDULE hooks are unavailable in the browser. This
 * is why `calibrate` is out of scope for the demo — it depends on them. Plain
 * `run` does not, because the patches are default-inert.
 */

/** Split the argv h3 builds back into the inputs the engine actually needs. */
export function parseHemInvocation(argv) {
  const inputPath = argv[1];
  if (!inputPath) throw new Error("HEM invocation had no input path");

  const weatherFlag = argv.indexOf("-e");
  const weatherPath = weatherFlag === -1 ? null : argv[weatherFlag + 1];

  const dir = inputPath.slice(0, inputPath.lastIndexOf("/"));
  const stem = inputPath.slice(inputPath.lastIndexOf("/") + 1).replace(/\.json$/, "");

  // h3 signals FHS assumptions by invoking the wrapper binary with
  // `--mode actual`, so that flag is the signal here rather than the binary
  // name — the WASM build has one entry point for both.
  const modeFlag = argv.indexOf("--mode");
  const fhsAssumptions = modeFlag !== -1 && argv[modeFlag + 1] === "actual";

  return {
    inputPath,
    weatherPath,
    resultsDir: `${dir}/${stem}__results`,
    heatBalance: argv.includes("--heat-balance"),
    fhsAssumptions,
    // h3 asks for the wrapper's preprocessed model, then flattens its heating
    // controls and runs the core engine on the result. That is how the demo
    // disables the FHS heating schedule, which the CLI does with an
    // environment variable that a wasm32 build cannot see.
    preprocessOnly: argv.includes("--preprocess-only"),
  };
}

/**
 * Build the executor h3's Python side calls once per offset.
 *
 * Reads the staged model and weather out of Pyodide's filesystem, hands them to
 * the engine, and writes the returned files back where h3 expects them.
 */
export function createHemBridge({ pyodide, runHem }) {
  return function runHemWasm(argv, _cwd, _env) {
    const args = argv?.toJs ? argv.toJs() : Array.from(argv);
    const {
      inputPath,
      weatherPath,
      resultsDir,
      heatBalance,
      fhsAssumptions,
      preprocessOnly,
    } = parseHemInvocation(args);

    const modelJson = pyodide.FS.readFile(inputPath, { encoding: "utf8" });
    const weatherEpw = weatherPath
      ? pyodide.FS.readFile(weatherPath, { encoding: "utf8" })
      : null;

    const files = runHem({
      modelJson,
      weatherEpw,
      heatBalance,
      fhsAssumptions,
      preprocessOnly,
    });
    if (!files || typeof files !== "object") {
      throw new Error("engine returned no output files");
    }

    pyodide.FS.mkdirTree(resultsDir);
    for (const [name, contents] of Object.entries(files)) {
      pyodide.FS.writeFile(`${resultsDir}/${name}`, contents);
    }
  };
}

/** Install the h3 wheel from an ArrayBuffer into a live Pyodide. */
export async function installH3(pyodide, wheelBytes, wheelName = H3_WHEEL_NAME) {
  if (!/^[A-Za-z0-9_.-]+\.whl$/.test(wheelName)) {
    throw new Error(`Invalid h3 wheel filename: ${wheelName}`);
  }
  await pyodide.loadPackage("micropip");
  const target = `/tmp/${wheelName}`;
  pyodide.FS.writeFile(target, new Uint8Array(wheelBytes));
  const micropip = pyodide.pyimport("micropip");
  await micropip.install(`emfs:${target}`);
  return micropip;
}

/**
 * Wire the two globals Python calls back into, for one run.
 *
 * Set per run rather than once at startup: `runHem` closes over the engine, and
 * a stale binding here would send a run's output to the previous caller.
 */
function installCallbacks({ pyodide, runHem, onProgress }) {
  globalThis.h3EmitEvent = (event) => {
    onProgress?.(
      event?.toJs ? event.toJs({ dict_converter: Object.fromEntries }) : event,
    );
  };
  globalThis.h3RunHem = createHemBridge({ pyodide, runHem });
}

/**
 * Design HTC for one set of sandbox attributes.
 *
 * One engine run per call, so this is the one the sliders drive. The attribute
 * names are the host page's own; `htc_harness.browser` owns the mapping to
 * levers, and rejects any name it does not know rather than ignoring it.
 */
export async function runSandboxDesign({ pyodide, runHem, values, onProgress }) {
  installCallbacks({ pyodide, runHem, onProgress });
  pyodide.globals.set("h3_values", pyodide.toPy(values));

  const raw = await pyodide.runPythonAsync(`
from js import h3EmitEvent, h3RunHem
from htc_harness import browser

browser.run_sandbox_design(
    values=h3_values.to_py() if hasattr(h3_values, "to_py") else dict(h3_values),
    run_hem_wasm=lambda argv, cwd, env: h3RunHem(argv, cwd, env),
    emit=lambda event: h3EmitEvent(event),
)
`);
  return JSON.parse(raw);
}

/**
 * The three-offset empirical estimate for one set of sandbox attributes.
 *
 * Materially slower than `runSandboxDesign` — three runs rather than one — which
 * is why the page makes it a separate action rather than part of a slider drag.
 */
export async function runSandboxEmpirical({
  pyodide,
  runHem,
  values,
  onProgress,
}) {
  installCallbacks({ pyodide, runHem, onProgress });
  pyodide.globals.set("h3_values", pyodide.toPy(values));

  const raw = await pyodide.runPythonAsync(`
from js import h3EmitEvent, h3RunHem
from htc_harness import browser

browser.run_sandbox_empirical(
    values=h3_values.to_py() if hasattr(h3_values, "to_py") else dict(h3_values),
    run_hem_wasm=lambda argv, cwd, env: h3RunHem(argv, cwd, env),
    emit=lambda event: h3EmitEvent(event),
)
`);
  return JSON.parse(raw);
}
