# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.

"""Verify that h3's engine patches are default-inert.

With no h3 environment variables set, a patched engine build must produce
results identical to an unpatched build at the same pinned commit. The only
permitted difference is the static output file, to which
`0004-static-output-enhanced` adds rows -- additively, never changing or
removing an existing row.

This property is what allows h3 and Vulcan to share a single engine build. If
this check fails, that sharing is no longer safe.

    python scripts/check_patch_inertness.py <model.json> <weather.epw>

Requires git, cargo and network access to clone the pinned engine. Builds two
release binaries, so expect several minutes on a cold cache.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STATIC_OUTPUT = "model__core__results_static.csv"

# Environment variables that switch h3's patches on. All must be absent for the
# inertness comparison to mean anything.
H3_ENV_VARS = ("HEM_ACH_CAL_FACTOR", "HEM_FHS_DISABLE_HEATING_SCHEDULE")


def run(command: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write((result.stdout or "") + (result.stderr or ""))
        raise SystemExit(f"command failed: {' '.join(command)}")


def build_engine(dest: Path, *, repo_url: str, commit: str, patches: list[str]) -> Path:
    run(["git", "init", "-q", str(dest)])
    run(["git", "remote", "add", "origin", repo_url], cwd=dest)
    run(["git", "fetch", "-q", "--depth", "1", "origin", commit], cwd=dest)
    run(["git", "checkout", "-q", commit], cwd=dest)
    for patch in patches:
        run(["git", "apply", str(ROOT / patch)], cwd=dest)
    run(["cargo", "build", "--release", "--features", "clap", "--bin", "hem"], cwd=dest)
    binary = dest / "target" / "release" / "hem"
    if not binary.exists():
        raise SystemExit(f"built binary not found: {binary}")
    return binary


def run_engine(binary: Path, model: Path, weather: Path, workdir: Path) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    model_copy = workdir / "model.json"
    shutil.copy(model, model_copy)
    env = {k: v for k, v in os.environ.items() if k not in H3_ENV_VARS}
    run_result = subprocess.run(
        [str(binary), str(model_copy), "-e", str(weather), "--heat-balance"],
        cwd=workdir,
        capture_output=True,
        text=True,
        env=env,
    )
    if run_result.returncode != 0:
        sys.stderr.write(run_result.stderr)
        raise SystemExit("engine run failed")
    return workdir / "model__results"


def compare(baseline: Path, patched: Path) -> list[str]:
    """Return a list of problems. Empty means the patches are inert."""
    problems: list[str] = []

    baseline_files = {p.name for p in baseline.iterdir() if p.is_file()}
    patched_files = {p.name for p in patched.iterdir() if p.is_file()}

    for missing in sorted(baseline_files - patched_files):
        problems.append(f"patched build did not produce {missing}")
    for added in sorted(patched_files - baseline_files):
        problems.append(f"patched build produced unexpected extra file {added}")

    for name in sorted(baseline_files & patched_files):
        baseline_text = (baseline / name).read_text()
        patched_text = (patched / name).read_text()
        if baseline_text == patched_text:
            continue
        if name != STATIC_OUTPUT:
            problems.append(f"{name} differs, but only {STATIC_OUTPUT} may differ")
            continue
        # The static file may only gain lines.
        removed = [
            line
            for line in difflib.ndiff(
                baseline_text.splitlines(), patched_text.splitlines()
            )
            if line.startswith("- ")
        ]
        if removed:
            problems.append(
                f"{name} removed or altered {len(removed)} existing row(s); "
                f"the enhanced static output must be purely additive"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path, help="HEM input JSON")
    parser.add_argument("weather", type=Path, help="EPW weather file")
    args = parser.parse_args()

    manifest = json.loads((ROOT / "patches" / "manifest.json").read_text())["rust"]
    repo_url, commit, patches = (
        manifest["repo_url"],
        manifest["commit"],
        manifest["patches"],
    )
    print(f"Engine: {repo_url} @ {commit}")
    print(f"Patches: {len(patches)}")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)

        print("\nBuilding patched engine...")
        patched_bin = build_engine(
            work / "patched", repo_url=repo_url, commit=commit, patches=patches
        )
        print("Building unpatched baseline...")
        baseline_bin = build_engine(
            work / "baseline", repo_url=repo_url, commit=commit, patches=[]
        )

        print("\nRunning both with no h3 environment variables set...")
        patched_out = run_engine(
            patched_bin, args.model, args.weather, work / "run" / "patched"
        )
        baseline_out = run_engine(
            baseline_bin, args.model, args.weather, work / "run" / "baseline"
        )

        problems = compare(baseline_out, patched_out)

    if problems:
        print("\nFAILED: patches are not default-inert.", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print("\nPASSED: patches are default-inert.")
    print(f"Only {STATIC_OUTPUT} differs, and only by added rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
