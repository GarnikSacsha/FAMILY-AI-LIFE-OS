# Bug Reproducer

## ✅ FIX_PROVEN — Bug reproduced and fix proven

> The approved task-list reproducer failed before the fix and passes after it; the relevant suite and full suite also pass.

**Project:** FAMILY-AI-LIFE-OS  
**Bug:** Explicit task requests are classified as calendar events  
**Environment:** Python 3.14.6, Windows, pytest, Europe/Kyiv  
**Generated:** 2026-08-10

## Discovery scope

- app/orchestration/orchestrator.py
- app/domains/identity/service.py
- app/telegram/bot.py
- tests/test_task_list_routing_regression.py
- existing planning, calendar, memory, security, and orchestrator tests

## Ranked and tested candidates

| # | Candidate | Contract evidence | Trigger | Location | Confidence | Outcome |
|---:|---|---|---|---|---|---|
| 1 | Explicit task marker is classified as calendar | Household planner operations are allowed in family groups; calendar is private-only. | Добавь в таски для меня на завтра.\n\nКупить порошок. | app/orchestration/orchestrator.py:198 | high | REPRODUCED |
| 2 | Multiline task list is not parsed into separate tasks | Each non-empty task line becomes a household task with the shared due date and requested assignee. | The supplied eight-line task list. | app/orchestration/orchestrator.py:1540 | high | FIX_PROVEN |

## Original report

In an authorized family group, a multiline request beginning with «Добавь в таски для меня на завтра» received the private-calendar access denial.

| Contract | Expected | Actual |
|---|---|---|
| Observed behavior | The request is authorized as planner work, creates eight household tasks, assigns them to the current user, and sets their due date to tomorrow. | Before the fix, domain_for_message returned calendar, causing the family-group authorization check to reject the request. |

## Minimal reproduction

A focused unit test calls MainOrchestrator.domain_for_message with the explicit task marker and expects planner.

**Confirming signal:** The pre-fix assertion received calendar instead of planner.

### Reproduction files approved at Gate 1

- [test_task_list_routing_regression.py](<C:/Users/Денис/Desktop/Всякое вайбкодинг/Просто тест всякое/FAMILY-AI-LIFE-OS/tests/test_task_list_routing_regression.py:12>) — Gate 1 regression and post-fix task-list coverage.

## Red to green evidence

| Evidence | Before fix | After fix |
|---|---:|---:|
| Exit code | 1 | 0 |
| Timed out | False | False |
| Duration | 5,600 ms | 8,297 ms |
| Same command | — | True |
| Broader suite | — | passed |

### Before — failing evidence

```text
F                                                                        [100%]
FAILED tests/test_task_list_routing_regression.py::test_explicit_task_marker_has_priority_over_calendar_domain - AssertionError: assert 'calendar' == 'planner'
1 failed, 1 warning in 3.46s
```

### After — fixed evidence

```text
...                                                                      [100%]
3 passed, 1 warning in 6.33s
```

## Root cause

Calendar classification ran before explicit task classification, and the shared-chat semantic calendar path could also claim explicit task requests.

## Approved fix

Added explicit task-marker precedence, skipped semantic calendar handling for explicit task requests, and added deterministic multiline task parsing with list cleanup, due-date extraction, and current-user assignment.

**Why this is causal:** The authorization domain is now planner before any calendar predicate can match, while the processing path reaches PlannerTools directly for explicit task lists.

### Production files approved at Gate 2

- [orchestrator.py](<C:/Users/Денис/Desktop/Всякое вайбкодинг/Просто тест всякое/FAMILY-AI-LIFE-OS/app/orchestration/orchestrator.py:185>) — Task-marker precedence, semantic-calendar guard, and list task creation.
- [test_task_list_routing_regression.py](<C:/Users/Денис/Desktop/Всякое вайбкодинг/Просто тест всякое/FAMILY-AI-LIFE-OS/tests/test_task_list_routing_regression.py:24>) — Regression coverage for eight tasks, tomorrow due date, and current-user assignment.

## Verification

| Check | Status | Evidence |
|---|---|---|
| Regression test | ✅ passed | Red before, green after: 3 passed. |
| Relevant suite | ✅ passed | 51 passed, 1 warning. |
| Full pytest suite | ✅ passed | 276 passed, 1 skipped, 1 warning. |
| Ruff | ✅ passed | All checks passed for changed Python files. |

## Reproduce

```bash
.venv\Scripts\python.exe -m pytest -q tests/test_task_list_routing_regression.py
```

## Limitations

- The regression uses the existing in-memory SQLite fixture; PostgreSQL execution is covered by the same SQLAlchemy model path but was not run here.

## Residual risks

- Natural-language task date phrases beyond today/tomorrow/after-tomorrow remain outside this focused change.

## Notes

- app/domains/identity/service.py was not changed; planner remains allowed in family groups and calendar remains private-only.
- Existing user modifications in .env.example, outputs/bug-reproducer-report.md, tests/test_voice_transcription.py, and .worktrees were preserved.

---

Generated by `$bug-reproducer`. A fix is proven only by the same red-to-green reproducer plus relevant broader checks.
