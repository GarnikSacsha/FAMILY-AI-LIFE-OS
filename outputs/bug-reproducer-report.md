# Bug Reproducer

## ✅ FIX_PROVEN — Bug reproduced and fix proven

> The same reproducer changed from failing to passing and broader checks passed.

**Project:** Family AI Life OS
**Bug:** Shared digest recursively mixed bot reports and unverified expenses
**Environment:** Python 3.13 on Windows, async SQLAlchemy test environment
**Generated:** 2026-07-28

## Original report

Telegram digests were an unreadable layered paragraph, repeated previous bot summaries, contradicted the database about coffee and other expenses, and a question such as «А сегодняшний кофе?» did not query PostgreSQL.

| Contract | Expected | Actual |
|---|---|---|
| Observed behavior | Each digest is short and sectioned, uses only new user messages as conversational input, lists money only from confirmed PostgreSQL transactions, and answers named-expense questions from the database. | Line breaks were normalized away; previous Family reports were summarized again; chat mentions were presented as confirmed expenses; named-expense questions fell through to the general LLM. |

## Minimal reproduction

Approved focused tests persist a multi-section digest, pass a prior Family report beside user messages, record «Кофе 95 грн», and ask «А сегодняшний кофе?».

**Confirming signal:** Two summary tests initially failed because formatting was flattened and bot output entered the next summarizer input. The coffee follow-up test then failed because FinanceTools.find_expenses was awaited zero times.

### Reproduction files approved at Gate 1

- [test_shared_memory.py](<C:\Users\Денис\Desktop\Всякое вайбкодинг\Просто тест всякое\FAMILY-AI-LIFE-OS\tests\test_shared_memory.py:1>) — Approved regressions for formatting, bot-report recursion, and database-grounded money.
- [test_finance_regressions.py](<C:\Users\Денис\Desktop\Всякое вайбкодинг\Просто тест всякое\FAMILY-AI-LIFE-OS\tests\test_finance_regressions.py:1>) — Approved regressions for recording coffee and looking up today's coffee in PostgreSQL.

## Red to green evidence

| Evidence | Before fix | After fix |
|---|---:|---:|
| Exit code | 1 | 0 |
| Timed out | False | False |
| Duration | 0 ms | 8,742.092 ms |
| Same command | — | True |
| Broader suite | — | passed |

### Before — failing evidence

```text
FF.                                                                      [100%]
FAILED tests/test_shared_memory.py::test_persisted_summary_preserves_visual_sections - stored summary collapsed all section line breaks into one paragraph
FAILED tests/test_shared_memory.py::test_summary_extraction_does_not_reprocess_bot_reports - summarizer input included the bot's previous Family report
2 failed, 1 passed, 6 deselected
```

### After — fixed evidence

```text
...                                                                      [100%]
3 passed, 6 deselected, 1 warning in 7.07s
```

## Root cause

SharedMemoryTools normalized summary whitespace; the idle worker sent both user and bot records to the extractor; the model-generated money section was not grounded in financial_transactions; the router had no named-expense lookup intent.

## Approved fix

Preserve digest line breaks, summarize user messages only, cap and deduplicate sections, replace the money section with PostgreSQL query results, route named-expense questions to a database lookup, and dismiss malformed legacy generated memory.

**Why this is causal:** The changes remove the recursive source records and unverified monetary source directly, while the new finance route reads the same transaction table used when an expense is recorded.

### Production files approved at Gate 2

- [memory_tools.py](<C:\Users\Денис\Desktop\Всякое вайбкодинг\Просто тест всякое\FAMILY-AI-LIFE-OS\app\tools\memory_tools.py:1>) — Preserves visual section breaks in persisted summaries.
- [conversation_summary_worker.py](<C:\Users\Денис\Desktop\Всякое вайбкодинг\Просто тест всякое\FAMILY-AI-LIFE-OS\app\infrastructure\integrations\conversation_summary_worker.py:1>) — Uses user messages only and grounds money in confirmed PostgreSQL transactions.
- [finance_tools.py](<C:\Users\Денис\Desktop\Всякое вайбкодинг\Просто тест всякое\FAMILY-AI-LIFE-OS\app\tools\finance_tools.py:1>) — Adds named-expense and time-window PostgreSQL queries.
- [orchestrator.py](<C:\Users\Денис\Desktop\Всякое вайбкодинг\Просто тест всякое\FAMILY-AI-LIFE-OS\app\orchestration\orchestrator.py:1>) — Answers named-expense questions with factual database results.
- [007_clean_legacy_shared_memory.py](<C:\Users\Денис\Desktop\Всякое вайбкодинг\Просто тест всякое\FAMILY-AI-LIFE-OS\alembic\versions\007_clean_legacy_shared_memory.py:1>) — Dismisses old unverified money and malformed generated memory without deleting raw chat or transactions.

## Verification

| Check | Status | Evidence |
|---|---|---|
| Summary red-to-green reproducer | ✅ passed | 2 failures before; focused summary tests pass after. |
| Finance and shared-memory regressions | ✅ passed | 16 passed. |
| Full test suite | ✅ passed | 168 passed, 1 skipped. |
| Ruff | ✅ passed | All checks passed. |
| mypy | ✅ passed | No issues in 40 source files. |
| Alembic | ✅ passed | 007_clean_legacy_shared_memory is the single head. |

## Reproduce

```bash
.venv\Scripts\python.exe -m pytest -q tests\test_shared_memory.py -k summary
```
```bash
.venv\Scripts\python.exe -m pytest -q tests\test_finance_regressions.py tests\test_shared_memory.py
```
```bash
.venv\Scripts\python.exe -m pytest -q
```

## Limitations

- The bot can confirm only expenses that were actually committed to PostgreSQL.
- Google Sheets remains an asynchronous mirror; its status is reported separately from the PostgreSQL commit.

## Residual risks

- A very unusual merchant name outside the current named-expense vocabulary may still require an explicit «покажи расход ...» phrase.

## Notes

- No personal-chat context is used.
- Raw group chat and real financial transactions are preserved by the cleanup migration.

---

Generated by `$bug-reproducer`. A fix is proven only by the same red-to-green reproducer plus relevant broader checks.
