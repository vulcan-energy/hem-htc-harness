# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.
from __future__ import annotations

import sys
from pathlib import Path


def pytest_configure() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
