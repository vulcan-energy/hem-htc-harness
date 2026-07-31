# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.

"""The dwelling the browser sandbox lets people edit, and the weather it runs in.

Both ship inside the wheel, so the host page fetches one artifact rather than
keeping model and weather files in step with the code that transforms them.

`semi_detached.json` is a Future Homes Standard model of a two-storey
semi-detached house: 71.2 m2, heat pump, continuous mechanical extract. It is
synthetic — authored for Vulcan's own test batches, not derived from a real
dwelling and carrying no measured data.

`london.epw` is the Home Energy Model's own London example weather year, MIT and
Crown Copyright like the engine itself (see NOTICE). It is here because the
model carries no weather of its own: `ExternalConditions` holds only shading
segments, and the FHS wrapper resolves an EPW into the model during
preprocessing.

The full 8760-hour year ships even though the sandbox simulates Jan-Mar: the
wrapper preprocesses against a whole year, and the run is shortened afterwards.
Trimming the file would move that decision somewhere it cannot be seen.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

SANDBOX_MODEL = "semi_detached.json"
SANDBOX_WEATHER = "london.epw"


def load_sandbox_model() -> dict[str, Any]:
    """Return the sandbox dwelling as a fresh dict.

    Fresh each call: callers mutate it through the lever transforms, and a
    shared instance would let one edit leak into the next run.
    """
    with resources.files(__name__).joinpath(SANDBOX_MODEL).open() as handle:
        return json.load(handle)


def load_sandbox_weather() -> str:
    """Return the sandbox weather year as EPW text."""
    return (
        resources.files(__name__).joinpath(SANDBOX_WEATHER).read_text(encoding="utf-8")
    )
