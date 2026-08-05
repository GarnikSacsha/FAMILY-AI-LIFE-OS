# Finance Bug Reproducer

## FIX_PROVEN

The same focused regression command changed from two failures to passing, and the full test suite passed.

## Bugs

1. A multiline expense message was parsed with search() as one transaction, so only the first amount reached PostgreSQL.
2. Manual Telegram input did not carry a stable message identity into FinanceTools, so replaying the same message could create a new UUID and a duplicate Google Sheets row.
3. Ukrainian fallback labels were incomplete, and транспорт could match the спорт substring.

## Fix

- Parse one amount and label per non-empty line.
- Accept the currency only on the first line and inherit it for subsequent amount-first lines.
- Preserve the legacy single-line format such as базар 1900 грн.
- Pass telegram_chat_id and telegram_message_id into the expense path.
- Generate telegram:<chat_id>:<message_id>:expense:<line_number> as the stable external identity.
- Add Ukrainian fallback labels and a dedicated Sports category.
- Keep the existing nine-column Sheets projection and UUID verification.

## Evidence

Before:

    2 failed
    Only 3900.0 was sent to Finance Agent instead of six line amounts.
    A replayed manual expense returned SUCCESS instead of DUPLICATE.

After:

    5 passed
    214 passed, 1 skipped, 1 warning

Targeted regression tests:

- tests/test_multiline_expenses.py
- tests/test_finance_input_regressions.py
- tests/test_orchestrator_agent_regressions.py

## Limitations

Existing duplicate rows in the live Google Sheet were not deleted. The code now prevents future replay duplicates; cleaning historical rows is a separate destructive operation requiring an explicit cleanup scope.

Full-project Ruff still reports one unrelated pre-existing line-length error at app/tools/mail_filter.py:148. Ruff passes for all changed files, and mypy passes for the application.
