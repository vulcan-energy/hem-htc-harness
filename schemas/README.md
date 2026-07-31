# Schemas in htc-harness

This folder contains JSON schema definitions for structured inputs used by `h3`.

## `measured_htc.schema.json`

Purpose:
- Define a stable format for a measured (or synthetic target) whole-building HTC value.
- Keep measurement provenance alongside the numeric target.

Current usage in h3:
- Used directly in calculations:
  - `htc_value_w_per_k` (target HTC for comparison/calibration)
  - `window` when `type = "index"` (can auto-set analysis indices if CLI `--window` is not supplied)
- Stored as metadata (not currently used in h3 maths):
  - `uncertainty_w_per_k`
  - `method_profile`
  - `conditions`
  - `source`
  - `notes`

Notes:
- `window.type = "date_range"` is accepted by schema validation, but current h3 analysis logic aligns rows via index-based windows.
- `schema_version` is for compatibility/version checks and must match the expected version.

## Why keep metadata fields now?

Even when not used in calculations today, these fields are useful for:
- traceability (where the target came from),
- reproducibility (which method/conditions produced the value),
- future enhancement (for example uncertainty-aware weighting or richer reporting).
