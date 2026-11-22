# Changelog

Notable changes to h3 will be recorded here.

## [Unreleased]

## [0.2.0] - 2026-11-22

### Changed

- The browser demo runs on the pinned HEM:FHS 1.0.0a7 target bundle (`run_target_stage`, preprocess then core) named by the bundle manifest's `engine_module`, instead of a dedicated export. Results are unchanged.

## [0.1.0] - 2026-07-31

### Added

- Initial public release: the `h3` CLI (`setup`, `doctor`, `run`, `calibrate`,
  `plot`, `config`, `build-hem`), the empirical HTC methodology and docs site,
  pinned HEM engine and FHS wrapper patches with the default-inert guarantee,
  and the fixed synthetic browser sandbox bundle.
