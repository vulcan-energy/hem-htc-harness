#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
"""Pre-flight sweep for identifiable data in tracked files.

This repository intentionally carries no real property models. Publishing is
irreversible — clones and forks happen within minutes — so this guards against
accidentally adding identifiable project data before a release.

It looks for the *shapes* identifiers take rather than a denylist of the ones
already found, because the ones already found are the ones already removed. The
July 2026 sweep found addresses hiding in a `GuideOverlay` filename, which no
denylist would have predicted.

    python scripts/check_no_identifiable_data.py

Exits non-zero on any finding. Add a deliberate exception to ALLOWED below, with
the reason.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Patterns that identify a person, a property or a developer's machine.
PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "uk-postcode",
        re.compile(r"\b[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}\b"),
        "a UK postcode identifies a property",
    ),
    (
        "street-address",
        re.compile(
            r"\b[0-9]{1,4}[ _-]?[A-Z][A-Za-z]{2,}[ _-]?"
            r"(St|Street|Rd|Road|Ave|Avenue|Ln|Lane|Cl|Close|Dr|Drive|"
            r"Way|Ter|Terrace|Grove|Court|Ct|Place|Pl)\b"
        ),
        "a street address identifies a property",
    ),
    (
        "home-directory",
        re.compile(r"/(?:Users|home)/[a-zA-Z][a-zA-Z0-9._-]*/"),
        "an absolute home directory leaks the machine it was produced on",
    ),
    (
        "overlay-reference",
        re.compile(r"overlays/[^\s,|\"']+"),
        "Vulcan canvas overlay filenames have carried addresses",
    ),
]

# Deliberate exceptions: (path suffix, pattern name, reason).
ALLOWED: list[tuple[str, str, str]] = [
    (
        "CLA-INDIVIDUAL.md",
        "uk-postcode",
        "Home Energy Foundry's own registered address",
    ),
    ("CLA-ENTITY.md", "uk-postcode", "Home Energy Foundry's own registered address"),
    (
        "scripts/check_no_identifiable_data.py",
        "*",
        "this file describes the patterns it searches for",
    ),
]

# Binary and vendored paths that are not ours to police.
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".pdf", ".whl", ".epw", ".ico")


_HEX_DIGITS = set("0123456789abcdefABCDEF")


def _is_false_positive(name: str, line: str, match: re.Match[str]) -> bool:
    """Filter matches that cannot be the thing the pattern is looking for.

    A CSS hex colour such as `#FF97FF` satisfies the UK postcode shape. Rather
    than allowlisting the generated reports — which would blind the check to a
    real postcode appearing in one — recognise the colour itself.
    """
    if name != "uk-postcode":
        return False
    text = match.group(0)
    if " " in text:
        return False
    if not set(text) <= _HEX_DIGITS:
        return False
    # An unspaced, all-hex candidate: a colour if it is written as one.
    before = line[: match.start()]
    return before.endswith("#") or before.endswith("0x")


def _is_allowed(path: str, pattern_name: str) -> bool:
    return any(
        path.endswith(suffix) and name in (pattern_name, "*")
        for suffix, name, _reason in ALLOWED
    )


def candidate_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [f for f in out.splitlines() if not f.endswith(SKIP_SUFFIXES)]


def main() -> int:
    findings: list[str] = []
    files = candidate_files()
    for rel in files:
        path = Path(rel)
        try:
            text = path.read_text(errors="replace")
        except (OSError, IsADirectoryError):
            continue
        for name, pattern, why in PATTERNS:
            if _is_allowed(rel, name):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                for match in pattern.finditer(line):
                    if _is_false_positive(name, line, match):
                        continue
                    findings.append(
                        f"  {rel}:{lineno}  [{name}] {match.group(0)!r}\n      {why}"
                    )
                    break

    if findings:
        print("identifiable-data sweep: FAILED\n")
        print("\n".join(findings))
        print(
            f"\n{len(findings)} finding(s). Remove them, or add a deliberate "
            "exception to ALLOWED with the reason."
        )
        return 1

    print(f"identifiable-data sweep: passed ({len(files)} candidate files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
