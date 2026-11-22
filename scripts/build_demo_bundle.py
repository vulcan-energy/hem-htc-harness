#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
"""Build the versioned browser-demo bundle used by CI and GitHub Releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SANDBOX_CONTRACT_VERSION = 2
# The pinned Vulcan HEM target bundle the worker drives, served same-origin by
# the site. a7, because the sandbox dwelling is an a7-schema model.
ENGINE_MODULE = "/hem-targets/rust-fhs-a7-62d3df70-c5ba2673-v1/preparation/hem.js"
REQUIRED_ENGINE_EXPORT = "run_target_stage"
ENGINE_EXPORT_ARITY = 3


def _single_wheel(dist_dir: Path) -> Path:
    wheels = sorted(dist_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError(
            f"expected exactly one wheel in {dist_dir}, found {len(wheels)}"
        )
    return wheels[0]


def build_demo_bundle(
    *,
    wheel: Path,
    worker: Path,
    runtime: Path,
    output_dir: Path,
    archive: Path,
    commit: str,
) -> dict[str, object]:
    """Create the directory artifact and the identical durable release ZIP."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("commit must be a full 40-character lowercase Git SHA")
    for source in (wheel, worker, runtime):
        if not source.is_file():
            raise FileNotFoundError(source)

    output_dir.mkdir(parents=True)
    for source in (wheel, worker, runtime):
        shutil.copy2(source, output_dir / source.name)

    manifest: dict[str, object] = {
        "wheel": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "commit": commit,
        "engine_module": ENGINE_MODULE,
        "requires_engine_export": REQUIRED_ENGINE_EXPORT,
        "engine_export_arity": ENGINE_EXPORT_ARITY,
        "sandbox_contract_version": SANDBOX_CONTRACT_VERSION,
    }
    (output_dir / "h3-demo.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, mode="x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(output_dir.iterdir()):
            bundle.write(path, arcname=path.name)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "demo-bundle")
    parser.add_argument("--archive", type=Path, default=ROOT / "h3-demo-bundle.zip")
    args = parser.parse_args()

    build_demo_bundle(
        wheel=_single_wheel(args.dist_dir),
        worker=ROOT / "demo/worker.js",
        runtime=ROOT / "demo/h3-runtime.js",
        output_dir=args.output_dir,
        archive=args.archive,
        commit=args.commit,
    )


if __name__ == "__main__":
    main()
