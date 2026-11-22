# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import types
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts/build_demo_bundle.py"
WORKFLOW = ROOT / ".github/workflows/ci.yml"


def _load_build_script() -> types.ModuleType:
    assert BUILD_SCRIPT.is_file(), "the demo bundle must have one shared generator"
    spec = importlib.util.spec_from_file_location("build_demo_bundle", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_demo_bundle_carries_the_v2_sandbox_contract(
    tmp_path: Path,
) -> None:
    build_demo_bundle = _load_build_script()
    wheel = tmp_path / "dist/htc_harness-0.1.0-py3-none-any.whl"
    wheel.parent.mkdir()
    wheel.write_bytes(b"wheel under test")
    worker = tmp_path / "demo/worker.js"
    worker.parent.mkdir()
    worker.write_text("// worker\n")
    runtime = worker.with_name("h3-runtime.js")
    runtime.write_text("// runtime\n")
    bundle = tmp_path / "demo-bundle"
    archive = tmp_path / "h3-demo-bundle.zip"

    manifest = build_demo_bundle.build_demo_bundle(
        wheel=wheel,
        worker=worker,
        runtime=runtime,
        output_dir=bundle,
        archive=archive,
        commit="a" * 40,
    )

    assert manifest == {
        "wheel": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "commit": "a" * 40,
        "engine_module": "/hem-targets/rust-fhs-a7-62d3df70-c5ba2673-v1/preparation/hem.js",
        "requires_engine_export": "run_target_stage",
        "engine_export_arity": 3,
        "sandbox_contract_version": 2,
    }
    assert json.loads((bundle / "h3-demo.json").read_text()) == manifest
    assert {path.name for path in bundle.iterdir()} == {
        wheel.name,
        "worker.js",
        "h3-runtime.js",
        "h3-demo.json",
    }
    with zipfile.ZipFile(archive) as bundle_zip:
        assert set(bundle_zip.namelist()) == {
            wheel.name,
            "worker.js",
            "h3-runtime.js",
            "h3-demo.json",
        }
        assert json.loads(bundle_zip.read("h3-demo.json")) == manifest


def test_ci_builds_one_bundle_and_publishes_tagged_release_asset() -> None:
    workflow = WORKFLOW.read_text()

    assert re.search(r"(?m)^\s+tags:\s*\[\"v\*\"\]\s*$", workflow)
    assert "python scripts/build_demo_bundle.py" in workflow
    assert "manifest = {" not in workflow
    assert "uses: actions/upload-artifact@" in workflow
    assert "retention-days: 90" in workflow
    assert "release:" in workflow
    assert "startsWith(github.ref, 'refs/tags/v')" in workflow
    assert workflow.count("contents: write") == 1
    assert "gh run download" in workflow
    assert "gh release create" in workflow
    assert "--verify-tag" in workflow
    assert "h3-demo-bundle.zip" in workflow

    action_refs = re.findall(r"uses: actions/[^@\s]+@([^\s#]+)", workflow)
    assert action_refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)
