# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
# Part of Vulcan Community. See NOTICE and ADDITIONAL_TERMS.md.

"""Library-level errors.

The library must not depend on `click`, so that it can run under Pyodide and be
embedded by callers that are not the CLI. These exceptions carry the same
distinctions `click` makes, and `cli.py` translates them back so that exit codes
and message formatting are unchanged for CLI users.
"""

from __future__ import annotations


class HarnessError(Exception):
    """Base class for expected, user-facing h3 failures.

    Translated to `click.ClickException` (exit code 1) by the CLI.
    """


class HarnessUsageError(HarnessError):
    """The caller combined options in a way that cannot work.

    Translated to `click.UsageError` (exit code 2) by the CLI.
    """


class HarnessParameterError(HarnessUsageError):
    """A single parameter value is malformed.

    Translated to `click.BadParameter` by the CLI.
    """
