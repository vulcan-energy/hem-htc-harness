# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from htc_harness import build_tools


def _write_manifest(root: Path) -> None:
    manifest = root / "patches" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "rust": {
                    "repo_url": "https://example.invalid/hem.git",
                    "commit": "a" * 40,
                    "patches": [],
                },
                "fhs": {
                    "repo_url": "https://example.invalid/fhs.git",
                    "commit": "b" * 40,
                    "patches": [],
                },
            }
        )
    )


def test_installed_layout_uses_packaged_patches_and_user_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "installed"
    resource_root = data_root / "share" / "htc-harness"
    _write_manifest(resource_root)
    fake_module = data_root / "lib/python3.12/site-packages/htc_harness/build_tools.py"

    monkeypatch.setattr(build_tools, "__file__", str(fake_module))
    monkeypatch.setattr(build_tools.sysconfig, "get_path", lambda _name: str(data_root))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    layout = build_tools.resolve_build_layout()

    assert layout.resource_root == resource_root
    assert layout.work_root == tmp_path / "cache" / "htc-harness"


def test_source_layout_keeps_existing_repo_workdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "checkout"
    _write_manifest(source_root)
    (source_root / "pyproject.toml").write_text("[project]\nname='htc-harness'\n")
    fake_module = source_root / "src/htc_harness/build_tools.py"

    monkeypatch.setattr(build_tools, "__file__", str(fake_module))

    layout = build_tools.resolve_build_layout()

    assert layout.resource_root == source_root
    assert layout.work_root == source_root


def test_rust_builder_separates_resources_from_writable_work_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource_root = tmp_path / "resources"
    work_root = tmp_path / "cache"
    _write_manifest(resource_root)

    def fake_prepare(*, work_dir: Path, **_kwargs: object) -> None:
        work_dir.mkdir(parents=True)

    def fake_run(command: list[str], cwd: Path | None) -> None:
        if command[:2] == ["cargo", "build"]:
            assert cwd is not None
            binary = cwd / "target/release/hem"
            binary.parent.mkdir(parents=True)
            binary.touch()

    monkeypatch.setattr(build_tools, "_prepare_patched_checkout", fake_prepare)
    monkeypatch.setattr(build_tools, "run_checked", fake_run)

    binary = build_tools.build_patched_rust_hem(resource_root, work_root=work_root)

    assert binary == work_root / ".patch-workdir/rust/target/release/hem"
    assert (work_root / ".patch-workdir/rust/patched_manifest.json").is_file()


def test_wheel_declares_every_build_resource() -> None:
    root = Path(__file__).resolve().parents[1]
    setuptools = tomllib.loads((root / "pyproject.toml").read_text())["tool"][
        "setuptools"
    ]

    assert setuptools["data-files"] == {
        "share/htc-harness/patches": ["patches/manifest.json"],
        "share/htc-harness/patches/rust": ["patches/rust/*.patch"],
        "share/htc-harness/patches/fhs": ["patches/fhs/*.patch"],
    }
