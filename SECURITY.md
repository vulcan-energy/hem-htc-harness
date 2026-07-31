# Security policy

## Reporting a vulnerability

**Do not open a public issue** for a suspected vulnerability, exposed credential,
customer data exposure or private-source disclosure.

Report it privately to `info@usevulcan.app` with:

- the affected component and version or commit;
- reproduction steps and expected impact;
- whether credentials, personal data or private source may be exposed; and
- a safe contact method for follow-up.

Home Energy Foundry Limited will acknowledge receipt, triage the report and agree
a disclosure approach proportionate to the risk.

Please do not access data that is not yours, degrade a service, persist access,
or disclose the issue before a remediation window has been agreed.

## Scope

This policy covers released h3 code and official public artifacts, including:

- the `h3` CLI and the `htc_harness` package;
- the patches under `patches/` and the HEM builds they produce;
- the browser demo bundle and its Pyodide/WASM worker.

The CLI is local-first and has no network egress by default beyond fetching the
pinned HEM source during `h3 setup` and `h3 build-hem`.

Vulnerabilities in the hosted Vulcan service or other Home Energy Foundry
products should use the same private contact but may follow a separate
remediation and disclosure process.

## Particular areas of interest

- **Input parsing.** h3 reads untrusted HEM JSON, EPW and CSV. Parser crashes are
  bugs; anything that escapes the intended file scope is a vulnerability.
- **The browser sandbox.** It runs a bundled synthetic model in Pyodide and
  WASM; it does not accept project uploads. Sandbox escapes or unexpected
  network transmission of sandbox state are in scope.
- **Build provenance.** `h3 build-hem` clones and patches upstream source. Report
  anything that would let a third party influence what gets built.

## Release integrity

Official releases identify their exact source revision, preserve licence and
origin notices, and pass the published CI gates: tests, lint, licensing,
attribution, the current-tree identifiable-data sweep, and the browser runtime
test. Forks must not represent themselves as official builds — see
[`TRADEMARKS.md`](TRADEMARKS.md).
