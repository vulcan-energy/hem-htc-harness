# Contributing to h3

Thanks for your interest in h3. Contributions are genuinely welcome — this
project exists because measuring the gap between a modelled and a measured
building should be done in the open, with a method anyone can inspect and
challenge.

Issues, bug reports, methodology discussion and interoperability feedback need no
paperwork at all. Code and substantive documentation need a contributor agreement
first; see [Contributor agreement](#contributor-agreement) below.

## What we especially want

- **Methodology challenges.** If you think the HTC estimate is biased, or a loss
  term is mis-attributed, open an issue. Bring a worked example if you can.
- **Validation against real measurements.** Co-heating, SmartHTC, Veritherm or
  any other measured HTC — comparisons against h3 are valuable, and we will
  publish credible ones.
- **HEM engine compatibility.** h3 pins a specific engine revision and patches
  it. Reports that a newer revision breaks something are very useful.
- **Bug reports with a reproducer.** A minimal HEM input JSON plus the command
  you ran beats a description every time.
- **Documentation.** If something in `docs/` misled you, that is a bug.

## What is out of scope

h3 reads Home Energy Model output and estimates an empirical HTC. It is not a
HEM implementation, a compliance tool, or a general building-physics library.
Changes that push it toward those are likely to be declined — not because they
are bad, but because they belong elsewhere.

## Development setup

**Prerequisites:** Python 3.12+, Rust/Cargo, Git.

```bash
git clone https://github.com/vulcan-energy/hem-htc-harness.git
cd hem-htc-harness
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev,plot]"
h3 setup
```

`h3 setup` checks the environment, clones the pinned HEM and FHS commits from
`patches/manifest.json`, applies the patches, builds both binaries, configures
paths, and runs `h3 doctor`. You do not need your own HEM checkout.

Manual setup, if you prefer:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev,plot]"
h3 build-hem
```

Run the tests:

```bash
pytest
```

The real HEM integration test is opt-in, because it needs a built binary:

```bash
HTC_HARNESS_RUNNER_INTEGRATION=1 HTC_HARNESS_HEM_BIN=/path/to/hem pytest
```

Lint and format before you push:

```bash
ruff check src tests scripts
ruff format src tests scripts
```

Lint rule selection is pinned explicitly in `pyproject.toml` under
`[tool.ruff.lint]`, so it does not drift with your ruff version.

Optionally install the repo's pre-push hook, which runs the same checks CI does:

```bash
git config core.hooksPath scripts/hooks
```

## Pull requests

- Branch from `main`. All changes land via pull request, and CI must pass.
- Keep the change focused. One concern per PR.
- Add or update tests for behaviour changes.
- If you change the HTC calculation, say so explicitly in the PR description and
  include before/after numbers on a fixture. Silent numeric drift is the thing we
  most need to catch.
- CI must pass, including the licence and attribution gate.

### Changes to the patches

`patches/` holds patches applied to a clean clone of the upstream HEM engine at
the commit pinned in `patches/manifest.json`. If you change them:

- keep them **default-inert** — with no h3 environment variables set, a patched
  build must produce results identical to an unpatched build at the same commit.
  Run `scripts/check_patch_inertness.py` against a representative model before
  release and include the result in the pull request;
- regenerate the patch files rather than hand-editing them;
- note the upstream commit you rebased against.

### Changes to licensing and attribution

Changes to `LICENSE`, `LICENSES/`, `NOTICE`, `ADDITIONAL_TERMS.md`,
`ATTRIBUTION.md`, `TRADEMARKS.md`, the contributor agreements or
`vulcan-origin.json` require explicit Home Energy Foundry owner approval. Please
open an issue before sending a PR that touches them.

## Contributor agreement

h3 is AGPL-3.0-only. Home Energy Foundry Limited also offers it under separate
commercial terms. For that to stay possible, contributed code needs a contributor
licence agreement.

- Individual contributors and co-authors: accept
  [`CLA-INDIVIDUAL.md`](CLA-INDIVIDUAL.md).
- If your employer owns or may own the contribution: an authorised representative
  must also execute [`CLA-ENTITY.md`](CLA-ENTITY.md) naming you.
- Obviously non-copyrightable fixes — an isolated typo, a broken link — can be
  exempted in writing by a maintainer.

Individual acceptance is automated. On your first pull request, the CLA check
posts a comment linking the agreement — read it, then reply with the acceptance
statement it specifies, exactly, from your own GitHub account. The check records
your GitHub handle, the date and the PR in `signatures/v1.1/cla.json` on the
`cla-signatures` branch of this repository and passes for all your future PRs.
A revised agreement starts a new signature file, so acceptance always names the
version accepted.

Entity agreements are handled by email, case by case: an authorised
representative signs `CLA-ENTITY.md`, attaches the covered-contributor schedule
specified there, and sends it to `info@usevulcan.app`. Home Energy Foundry
Limited countersigns and returns the copy by email, and covered contributors are
added to the CLA check's allowlist so their pull requests pass automatically.

If you cannot use the automated process, sign the applicable document and email
it privately to `info@usevulcan.app` with your GitHub handle and the PR number.
**Do not commit signed agreements or personal addresses to the repository.**

These agreements are based on the Harmony 1.0 templates, using the
copyright-**licence** model with outbound Option Five. In plain terms: **you keep ownership of your contribution.**
You grant Home Energy Foundry Limited broad, irrevocable rights to use and
sublicense it, including commercially — and in return, your contribution must
always remain available under the licence of the material you submitted it to. It
cannot be taken proprietary-only.

## Pull-request declaration

Every contributor confirms in the PR that:

1. they wrote the contribution, or have identified every third-party portion and
   have written approval to submit it;
2. they have authority to grant the rights in the applicable CLA, including any
   employer permission needed;
3. the contribution contains no employer, customer or other confidential
   material — **including building data you do not have permission to publish**;
4. all dependencies and copied material are identified with their licences;
5. materially AI-generated or AI-transformed content is identified, naming the
   tool and the files affected; and
6. all co-authors are named and covered by the appropriate agreements.

The PR template covers these as a checklist.

Disclosure does not by itself make third-party or generated material acceptable.
Maintainers still verify that both the public AGPL distribution and Home Energy
Foundry's parallel commercial use are permitted.

## A note on building data

Please never attach real, identifiable building data — addresses, occupier
details, or client models you do not own — to an issue or PR. Anonymise or
synthesise. Once it is in git history it is very hard to remove.

## Code of conduct

Participation is governed by [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## Security

Do not open a public issue for a vulnerability. See [`SECURITY.md`](SECURITY.md).

## Governance, honestly stated

h3 is maintained by Home Energy Foundry Limited, which retains discretion over
what is accepted and is not obliged to merge any contribution. We will always
tell you why something is declined. If a change is a better fit as a fork, we
will say so plainly rather than leaving a PR to rot.
