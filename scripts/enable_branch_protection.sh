#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Home Energy Foundry Limited
#
# Enable branch protection on main.
#
# NOTE: GitHub Free does not allow branch protection or rulesets on *private*
# repositories. Until this repo is either made public or the account is on
# GitHub Pro, this script will fail with HTTP 403. Both paths are fine; the
# Run this only after the reviewed clean-root repository has been created.
#
# Usage:  ./scripts/enable_branch_protection.sh [owner/repo]

set -euo pipefail

REPO="${1:-vulcan-energy/hem-htc-harness}"
BRANCH="main"

echo "Applying branch protection to ${REPO}@${BRANCH}..."

gh api -X PUT "repos/${REPO}/branches/${BRANCH}/protection" \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["test"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": true,
  "required_conversation_resolution": true
}
JSON

echo "Enabling automatic branch deletion on merge..."
gh api -X PATCH "repos/${REPO}" -f delete_branch_on_merge=true >/dev/null

echo "Done. Current protection:"
gh api "repos/${REPO}/branches/${BRANCH}/protection" \
  --jq '{
    reviews: .required_pull_request_reviews.required_approving_review_count,
    checks: .required_status_checks.contexts,
    force_pushes: .allow_force_pushes.enabled,
    deletions: .allow_deletions.enabled,
    linear_history: .required_linear_history.enabled
  }'
