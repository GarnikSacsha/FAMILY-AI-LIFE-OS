# Repository maintenance status

Snapshot: 2026-11-10. Live PR status and deployment state take precedence over
this dated record.

## Released foundation

PR #30 was merged as 7d62ea0 after its exact head passed quality, container, and
secrets. Railway deployed that commit successfully. After singleton handover,
Telegram polling reconnected, one bot replica remained active, and /live and
/health returned HTTP 200. The previous deployment remained available for
rollback. Readiness does not prove live Sheets or Oura behavior end to end.

The release includes the README rewrite, synthetic Telegram illustration,
negated task-completion fix, and removal of 26 tracked diagnostic output files.
Local copies were preserved. Thirteen integrated remote branches were removed
after ancestry/patch-equivalence checks. Main blocks force pushes and deletion,
requires up-to-date quality/container/secrets checks, and applies protection to
admins. Required review approval count is zero.

## Dependency maintenance batch

[PR #28](https://github.com/GarnikSacsha/FAMILY-AI-LIFE-OS/pull/28) preserves and
combines the original branch histories below for a single reviewed rollout.
Each individual update passed hosted CI on the new main baseline. The combined
head must pass its own CI before merge; individual results are not a substitute.

| Original PR | Change | Evidence and conditions |
| --- | --- | --- |
| #6 | Redis client range below 9 | Conflict resolved while retaining Pillow 13 and cryptography 50. No Redis client calls found in app or tests; REDIS_URL is a configuration setting. |
| #8 | Mypy range below 3 | Updated type checker passed application checks. |
| #9 | Google GenAI range below 3 | App uses GenerateContent, not Interactions. Upstream v2.0 breaking changes concern Interactions. Offline provider tests passed individually; live provider behavior remains a release check. |
| #12 | checkout 7.0.1 | Full-SHA action pin; all three jobs passed. |
| #14 | setup-python 7.0.0 | Full-SHA action pin; removed pip-install input is not used. |
| #28 | setuptools range below 85 | Installation, wheel/container build, and audits passed individually. |
| #29 | Python 3.14.8 container | Both Python 3.12 and 3.14 quality suites, PostgreSQL migrations, and container checks passed in run 42841590217. |

The quality matrix now checks Python 3.12 and 3.14. A stable required job named
quality depends on the entire matrix and fails if either version fails or is
cancelled. This preserves the existing main protection requirement.

Merge the batch with ancestry preserved. Only remove original remote branches
after confirming their heads are ancestors of main and their PRs are integrated.
Do not delete local worktrees or uncommitted files during remote branch cleanup.

## Release procedure

The user authorized completing maintenance and rolling out checked changes.
Railway follows main automatically. Confirm current-head CI, merge once, then
verify the actual deployed commit, readiness, startup logs, and one polling
instance. Keep the prior deployment available for rollback. Application rollback
does not reverse database or external-service mutations; do not downgrade the
production database as a routine rollback step.

## Historical privacy remains separate

The current tracked tree no longer contains outputs. Earlier commits, PR diffs,
caches, and clones can still contain prior documents and user-derived diagnostic
text. Pattern scanning did not establish a live credential leak and cannot prove
that all personal data is absent. Intentional public operator contact details
remain in the public pages.

A history cleanup would require a private backup, an exact reviewed path/text
removal list, a rewrite in an isolated clone, validation of the rewritten tree,
and coordinated replacement of published refs. It changes commit IDs and may
need GitHub support for cached or pull-request references. It cannot erase
copies already downloaded by others. No history rewrite, forced push, repository
visibility change, or local data deletion has been performed or approved as part
of the deployment permission. Those actions require a concrete separate decision.
