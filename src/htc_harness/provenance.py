# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.

"""Engine provenance recorded alongside every summary.

Best-effort metadata supporting reproducibility: which Rust binary,
which pinned engine commit, and which flags and environment overrides were in
force. Reads the patched manifest written by `h3 build-hem`; starts no
subprocess.
"""

from __future__ import annotations

import json
from pathlib import Path

from .build_tools import patched_manifest_path_for_bin


def engine_provenance(
    *,
    hem_bin: Path | None,
    require_patched: bool,
    env_overrides: dict[str, str] | None = None,
    extra_flags: list[str] | None = None,
) -> dict[str, object]:
    out: dict[str, object] = {
        "runner": "rust",
        "require_patched": bool(require_patched),
        "env_overrides": dict(env_overrides) if env_overrides else {},
        "extra_flags": list(extra_flags) if extra_flags else [],
    }
    out["hem_bin"] = str(hem_bin) if hem_bin is not None else None
    if hem_bin is None:
        return out
    manifest_path = patched_manifest_path_for_bin(hem_bin)
    out["patched_manifest_path"] = str(manifest_path) if manifest_path else None
    if manifest_path and manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            if isinstance(manifest, dict):
                out["patched_manifest_commit"] = manifest.get("commit")
        except Exception:
            pass
    return out


def attach_engine_provenance_to_payload(
    payload: dict[str, object],
    *,
    hem_bin: Path | None,
    require_patched: bool,
    env_overrides: dict[str, str] | None = None,
    extra_flags: list[str] | None = None,
) -> None:
    job = payload.get("job")
    if not isinstance(job, dict):
        return
    job["engine"] = engine_provenance(
        hem_bin=hem_bin,
        require_patched=require_patched,
        env_overrides=env_overrides,
        extra_flags=extra_flags,
    )
