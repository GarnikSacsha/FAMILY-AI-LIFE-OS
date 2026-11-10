# Repository maintenance status

Snapshot: 2026-11-10. This is a dated review record, not live deployment status.

## What a pull request means

A pull request proposes changes from a branch into another branch. Opening one
or passing its checks does not apply it to production. In this repository,
merging into main can trigger Railway deployment automatically.

## Completed housekeeping

- Thirteen remote branches were removed after checking ancestry or equivalent
  patches and merged pull requests. Local worktrees were preserved.
- Main requires quality, container, and secrets checks with an up-to-date branch;
  force pushes and branch deletion are blocked, including for administrators.
  The configured required approval count is zero.
- PR #30 rewrites the README, labels its Telegram illustration as synthetic,
  removes 26 tracked diagnostic files while preserving local copies, and
  anonymizes selected public documents.
- PR #30 also fixes automatic completion for supported negative statements,
  including mixed positive/negative task messages.

## Open pull requests and next actions

| PR | Purpose | Review outcome / next action |
| --- | --- | --- |
| [#30](https://github.com/GarnikSacsha/FAMILY-AI-LIFE-OS/pull/30) | Consolidated fixes and public documentation | Release candidate. Commit 217ac7a passed quality, container, and secrets in CI run 42828757108. Later commits need their own checks. Merge and rollout remain pending. |
| [#8](https://github.com/GarnikSacsha/FAMILY-AI-LIFE-OS/pull/8) | Mypy major-range update | Refresh after the baseline fix and rerun checks. |
| [#9](https://github.com/GarnikSacsha/FAMILY-AI-LIFE-OS/pull/9) | Google GenAI major-range update | Refresh and retest; offline checks do not establish live provider compatibility. |
| [#12](https://github.com/GarnikSacsha/FAMILY-AI-LIFE-OS/pull/12) | checkout action major update | Refresh after the baseline fix and rerun checks. |
| [#14](https://github.com/GarnikSacsha/FAMILY-AI-LIFE-OS/pull/14) | setup-python action major update | Refresh after the baseline fix and rerun checks. |
| [#28](https://github.com/GarnikSacsha/FAMILY-AI-LIFE-OS/pull/28) | setuptools development dependency range | Refresh after the baseline fix and rerun checks. |
| [#6](https://github.com/GarnikSacsha/FAMILY-AI-LIFE-OS/pull/6) | Redis client major-range update | Resolve the recorded conflict and review client compatibility separately. |
| [#29](https://github.com/GarnikSacsha/FAMILY-AI-LIFE-OS/pull/29) | Python 3.12 to 3.14 runtime | Separate runtime upgrade: test on the proposed Python version, not only the current Python 3.12 CI environment. |

The failed runs for #8, #9, #12, #14, and #28 shared a Pillow 12.3.0 audit
blocker inherited from main. PR #30 contains the Pillow 13 baseline fix.
That does not establish that the proposed major upgrades are safe to merge.
Keep these PRs open until their own updated branches are reviewed and checked.

## Release boundary

At this snapshot, main remained at 3312fa3 and PR #30 was a draft. Railway's
production source followed main with checkSuites=false. The existing bot and
PostgreSQL service were Online, one running replica each, with no reported
recent failures or pending work. No rollout was performed by this cleanup.

Before release, review [RELEASE.md](RELEASE.md) and
[RELEASE_VALIDATION.md](RELEASE_VALIDATION.md), confirm the candidate's current
checks, and obtain authorization for the production rollout caused by merging.
Preserve a usable previous deployment and confirm rollback availability.
Monitor readiness, startup logs, and one Telegram polling instance after release.
Live integration behavior still needs checking; HTTP health alone cannot prove it.
Do not downgrade the production database as a routine application rollback.

## Public-data limitation

Removing files from the candidate does not erase prior commits, the current
main tree, PR diffs, caches, or existing clones. History rewriting and changing
repository visibility were not performed. Treat historical privacy remediation
as a separate decision with a backup and coordination plan.
