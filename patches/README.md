# Patch workflow (reproducible)

This folder stores patch files applied to **clean clones** of the public Rust
HEM engine and FHS wrapper. Repo URLs and commit SHAs are pinned in
`patches/manifest.json`.

## Structure

- `patches/rust/*.patch` — Rust HEM engine patches (apply in order)
- `patches/fhs/*.patch` — Future Homes Standard wrapper patches (apply in order).
  From engine v1.0.0-alpha the FHS wrapper lives in its own repository,
  `epb-hem-wrapper-fhs`, and builds to a separate `future-homes-standard`
  binary. `h3 build-hem` builds it against the patched engine so both share one
  engine build.
- `patches/manifest.json` — source repo URL + commit SHA + patch list per source;
  for the FHS pin, it also records the later Crown MIT declaration and canonical
  licence-file revisions

Patches within a source apply **in order** and may depend on each other:
`0002-ach-calibration` modifies a line that `0001-actual-ach` introduces, so it
will not apply to a pristine checkout on its own.

`scripts/check_licensing.py` enforces that every patch named in the manifest
exists on disk, and that every patch on disk is declared in the manifest.

## Default-inert requirement

The Rust patches must be **default-inert**: with no h3 environment variables set,
a patched build must produce results identical to an unpatched build at the same
commit. This is what allows Vulcan and h3 to share a single engine build.

| Patch | Gate |
|---|---|
| `0001-actual-ach` | Recording gated on `detailed_output_heating_cooling`; `ach_actual` is a rename |
| `0002-ach-calibration` | `HEM_ACH_CAL_FACTOR`, defaults to `1.0` |
| `0004-static-output-enhanced` | Additive output only; arithmetic unchanged |
| `fhs/0001-disable-heating-schedule` | `HEM_FHS_DISABLE_HEATING_SCHEDULE`, defaults off |

Verify inertness against a real model:

```bash
python scripts/check_patch_inertness.py model.json weather.epw
```

It builds a patched and an unpatched engine at the pinned commit, runs both with
no h3 environment variables set, and fails if anything other than added rows in
`results_static.csv` differs.

## Apply + build (local)

From the `htc-harness` repo root:

```
h3 build-hem
```
