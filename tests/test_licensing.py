# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FHS_LICENSE_DECLARATION_REVISION = "c22c467461fb971051b42e7fdd698bbfb03b7811"
FHS_LICENSE_EVIDENCE_REVISION = "dd5ba73a19674d631da59b4924bb7dc2833fbb3b"


def test_fhs_mit_license_evidence_is_pinned_and_bundled() -> None:
    manifest = json.loads((ROOT / "patches/manifest.json").read_text())
    origin = json.loads((ROOT / "vulcan-origin.json").read_text())
    fhs_origin = next(
        entry
        for entry in origin["third_party"]
        if entry["component"] == "Home Energy Model Future Homes Standard wrapper"
    )
    license_path = ROOT / "LICENSES/MIT-hem-wrapper-fhs.txt"

    assert manifest["fhs"]["license_declaration_revision"] == (
        FHS_LICENSE_DECLARATION_REVISION
    )
    assert manifest["fhs"]["license_evidence_revision"] == (
        FHS_LICENSE_EVIDENCE_REVISION
    )
    assert fhs_origin["license_text"] == "LICENSES/MIT-hem-wrapper-fhs.txt"
    assert fhs_origin["license_declaration_revision"] == (
        FHS_LICENSE_DECLARATION_REVISION
    )
    assert fhs_origin["license_evidence_revision"] == FHS_LICENSE_EVIDENCE_REVISION

    license_text = license_path.read_text()
    assert license_text.startswith("The MIT License (MIT)\n")
    assert "Copyright (c) 2026 Crown Copyright" in license_text
    assert "Permission is hereby granted, free of charge" in license_text
    assert "THE SOFTWARE IS PROVIDED “AS IS”" in license_text
