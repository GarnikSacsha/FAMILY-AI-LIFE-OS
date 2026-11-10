# Bug Reproducer: Oura connection ignored

## FIX_PROVEN

The same focused reproducer changed from three failing tests to twelve passing tests, and the broader suite passed.

Project: Family AI Life OS
Generated: 2026-07-26

## Original report

The Oura callback displayed `Oura Ring connected`, but the Telegram assistant said it could not see the connection. Repeating `/oura` always generated another authorization link.

Expected:

- A connected user receives deterministic connection status.
- Repeating `/oura` reports the existing connection.
- Questions about today's sleep use Oura data instead of a generic LLM response.

Actual:

- The callback saved encrypted tokens, but no downstream tool consumed them.
- Health intents fell through to the general LLM.
- `/oura` unconditionally created a new OAuth state.

## Red-to-green evidence

Before:

```text
FFF
Expected connection status or daily summary tools to be awaited once.
Awaited 0 times.
3 failed
```

After:

```text
............ [100%]
12 passed in 3.43s
```

Regression test: `tests/test_oura_health_integration.py`

## Root cause

The Oura workflow ended after encrypted token persistence. There was no connection-status query, rotating-token refresh, daily collection reader, or health-intent branch using the stored OAuth record.

## Approved fix

- `app/integrations/oura/client.py`: rotating refresh-token flow and allowlisted Oura V2 daily reads.
- `app/tools/health_tools.py`: per-user status, encrypted token lifecycle, and daily score aggregation.
- `app/orchestration/orchestrator.py`: deterministic Oura status and health summaries.
- `app/telegram/bot.py`: connection-aware `/oura` plus `/oura reconnect`.

The access and refresh tokens remain encrypted and bound to the internal Telegram user.

## Verification

| Check | Result |
|---|---|
| Focused Oura regression | 12 passed |
| Full pytest suite | 138 passed, 1 skipped |
| Branch coverage | 81.16%, required 80% |
| Ruff | Passed |
| mypy | Passed |
| Bandit | Passed |
| Alembic head | `004_google_workspace_sync` |

## Limitations and residual risks

- The conversational summary currently covers the user's current local day.
- Today's scores may be absent until the Oura mobile app synchronizes the ring.
- Reads are on demand; Oura webhooks are not implemented.
- OAuth refresh and the PostgreSQL commit cannot form one distributed transaction.

Machine-readable evidence: `outputs/oura-bug-reproducer-evidence.json`.
