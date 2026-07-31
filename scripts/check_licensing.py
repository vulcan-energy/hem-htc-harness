# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.

"""Licensing and attribution gate for h3.

Run in CI on every pull request:

    python scripts/check_licensing.py

Run before cutting a public release, which additionally forbids unresolved
placeholders:

    python scripts/check_licensing.py --release

This is the Python equivalent of the `validate.mjs` gate in the
`vulcan-community-v1` pack, kept in Python so h3's CI does not need Node.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PLACEHOLDER = "TO_BE_SET_BEFORE_RELEASE"

SPDX_TAG = "SPDX-License-Identifier: AGPL-3.0-only"

REQUIRED_FILES = (
    "LICENSE",
    "NOTICE",
    "ADDITIONAL_TERMS.md",
    "ATTRIBUTION.md",
    "TRADEMARKS.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "CLA-INDIVIDUAL.md",
    "CLA-ENTITY.md",
    "vulcan-origin.json",
    "LICENSES/AGPL-3.0.txt",
    "LICENSES/MIT-home-energy-model.txt",
    "LICENSES/MIT-hem-wrapper-fhs.txt",
    ".github/PULL_REQUEST_TEMPLATE.md",
)

# Marker lines that must appear in a verbatim AGPL-3.0 text.
AGPL_MARKERS = (
    "GNU AFFERO GENERAL PUBLIC LICENSE",
    "Version 3, 19 November 2007",
    "13. Remote Network Interaction; Use with the GNU General Public License.",
)

SOURCE_ROOTS = ("src", "tests", "scripts")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.notes: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def note(self, message: str) -> None:
        self.notes.append(message)


def check_required_files(report: Report) -> None:
    for name in REQUIRED_FILES:
        if not (ROOT / name).is_file():
            report.error(f"missing required file: {name}")


def check_licence_text(report: Report) -> None:
    licence = ROOT / "LICENSE"
    if not licence.is_file():
        return
    text = licence.read_text(encoding="utf-8")
    for marker in AGPL_MARKERS:
        if marker not in text:
            report.error(
                f"LICENSE does not look like a verbatim AGPL-3.0 text "
                f"(missing marker: {marker!r})"
            )
    vendored = ROOT / "LICENSES" / "AGPL-3.0.txt"
    if vendored.is_file() and vendored.read_text(encoding="utf-8") != text:
        report.error("LICENSE and LICENSES/AGPL-3.0.txt differ; they must be identical")

    fhs_mit = ROOT / "LICENSES" / "MIT-hem-wrapper-fhs.txt"
    if fhs_mit.is_file():
        fhs_text = fhs_mit.read_text(encoding="utf-8")
        for marker in (
            "The MIT License (MIT)",
            "Copyright (c) 2026 Crown Copyright",
            "Permission is hereby granted, free of charge",
        ):
            if marker not in fhs_text:
                report.error(
                    "LICENSES/MIT-hem-wrapper-fhs.txt does not look like the "
                    f"canonical FHS MIT text (missing marker: {marker!r})"
                )


def check_spdx_headers(report: Report) -> None:
    for root in SOURCE_ROOTS:
        base = ROOT / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            head = path.read_text(encoding="utf-8")[:512]
            if SPDX_TAG not in head:
                report.error(f"missing SPDX header: {path.relative_to(ROOT)}")


def load_json(path: Path, report: Report) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        report.error(f"missing file: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        report.error(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")
    return None


def check_manifest_patches(report: Report) -> dict | None:
    """Every patch the manifest names must exist on disk, and vice versa."""
    manifest_path = ROOT / "patches" / "manifest.json"
    manifest = load_json(manifest_path, report)
    if manifest is None:
        return None

    declared: set[str] = set()
    for runner, entry in manifest.items():
        if not isinstance(entry, dict):
            continue
        for rel in entry.get("patches", []):
            declared.add(rel)
            if not (ROOT / rel).is_file():
                report.error(
                    f"patches/manifest.json declares {rel!r} for runner "
                    f"{runner!r}, but that file does not exist"
                )

    for path in sorted((ROOT / "patches").rglob("*.patch")):
        rel = str(path.relative_to(ROOT))
        if rel not in declared:
            report.error(
                f"{rel} exists on disk but is not declared in patches/manifest.json"
            )

    return manifest


def check_origin_matches_manifest(manifest: dict | None, report: Report) -> None:
    """vulcan-origin.json must not drift from the authoritative manifest."""
    origin = load_json(ROOT / "vulcan-origin.json", report)
    if origin is None or manifest is None:
        return

    if origin.get("license") != "AGPL-3.0-only":
        report.error("vulcan-origin.json: license must be 'AGPL-3.0-only'")

    for entry in origin.get("third_party", []):
        authority = entry.get("revision_authority")
        if not authority or "#" not in authority:
            continue
        _, _, pointer = authority.partition("#")
        section, _, field = pointer.partition(".")
        expected = (manifest.get(section) or {}).get(field)
        if expected is None:
            report.note(
                f"vulcan-origin.json points at {authority!r}, which is not yet "
                f"present in patches/manifest.json"
            )
            continue
        actual = entry.get("revision")
        if actual != expected:
            report.error(
                f"vulcan-origin.json revision for {entry.get('component')!r} is "
                f"{actual!r} but patches/manifest.json {authority} is {expected!r}"
            )

    fhs_manifest = manifest.get("fhs") or {}
    fhs_origin = next(
        (
            entry
            for entry in origin.get("third_party", [])
            if entry.get("component")
            == "Home Energy Model Future Homes Standard wrapper"
        ),
        None,
    )
    if fhs_origin is None:
        report.error("vulcan-origin.json: missing FHS wrapper entry")
        return
    if fhs_origin.get("license_text") != "LICENSES/MIT-hem-wrapper-fhs.txt":
        report.error("vulcan-origin.json: FHS wrapper has the wrong license_text")
    for field in ("license_declaration_revision", "license_evidence_revision"):
        expected = fhs_manifest.get(field)
        actual = fhs_origin.get(field)
        if expected is None or actual != expected:
            report.error(
                f"vulcan-origin.json FHS {field} is {actual!r} but "
                f"patches/manifest.json records {expected!r}"
            )


def check_placeholders(report: Report, *, release: bool) -> None:
    offenders: list[str] = []
    for name in REQUIRED_FILES:
        path = ROOT / name
        if not path.is_file():
            continue
        if PLACEHOLDER in path.read_text(encoding="utf-8"):
            offenders.append(name)

    if not offenders:
        return
    listed = ", ".join(offenders)
    if release:
        report.error(f"unresolved {PLACEHOLDER} placeholders in: {listed}")
    else:
        report.note(
            f"unresolved {PLACEHOLDER} placeholders in: {listed} "
            f"(must be resolved before the public release)"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release",
        action="store_true",
        help="apply the stricter release gate (forbids unresolved placeholders)",
    )
    args = parser.parse_args()

    report = Report()
    check_required_files(report)
    check_licence_text(report)
    check_spdx_headers(report)
    manifest = check_manifest_patches(report)
    check_origin_matches_manifest(manifest, report)
    check_placeholders(report, release=args.release)

    mode = "release" if args.release else "standard"
    for note in report.notes:
        print(f"note: {note}")
    for error in report.errors:
        print(f"error: {error}", file=sys.stderr)

    if report.errors:
        print(
            f"\nlicensing gate ({mode}): FAILED with {len(report.errors)} error(s)",
            file=sys.stderr,
        )
        return 1

    print(f"\nlicensing gate ({mode}): passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
