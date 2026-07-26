# Bug Reproducer

## ✅ FIX_PROVEN — Bug reproduced and fix proven

The same reproducer changed from failing to passing, and the broader checks passed.

Project: Family AI Life OS

Generated: 2026-07-26

## Original report

Ordinary Telegram messages received the same scripted response. A request to record a 1900 UAH market expense also performed no action.

Expected:

- General messages receive a relevant LLM response.
- Natural-language expenses are validated and saved through the finance tools.

Actual:

- The hard-coded fallback was returned.
- The finance agent was never invoked.

## Minimal reproduction

The approved regression tests call the real `MainOrchestrator` with:

1. A general conversational message.
2. `Запиши пожалуйста мои траты сегодняшние, базар 1900 грн`.

External AI calls are mocked. The initial run failed with:

```text
Expected the mocked reasoning response, received the static fallback template.
Expected FinanceAgent to be awaited once, awaited 0 times.
2 failed
```

After the fix:

```text
2 passed in 0.85s
```

Regression test: [test_orchestrator_agent_regressions.py](<C:/Users/Денис/Desktop/Всякое вайбкодинг/Просто тест всякое/FAMILY-AI-LIFE-OS/tests/test_orchestrator_agent_regressions.py:1>)

## Root cause

`MainOrchestrator` contained deterministic branches but never connected its general fallback to `TerraReasoningProvider`. Its financial branch implemented summaries but not transaction logging.

## Approved fix

- [orchestrator.py](<C:/Users/Денис/Desktop/Всякое вайбкодинг/Просто тест всякое/FAMILY-AI-LIFE-OS/app/orchestration/orchestrator.py:1>) routes ordinary conversation to the LLM and expenses to validated finance tooling.
- [provider.py](<C:/Users/Денис/Desktop/Всякое вайбкодинг/Просто тест всякое/FAMILY-AI-LIFE-OS/app/integrations/llm/provider.py:138>) uses the Responses API, extracts output text, and disables server-side response storage.

Database writes still pass through `FinanceAgent` and `FinanceTools`; the model does not write directly.

## Verification

| Check | Status | Evidence |
|---|---|---|
| Focused regressions | Passed | 2 failed before, 2 passed after |
| Full test suite | Passed | 103 passed, 1 skipped |
| Ruff | Passed | Formatting and lint clean |
| mypy | Passed | No typing issues |
| Bandit | Passed | No reported security findings |

## Limitations and residual risks

- Conversation is currently stateless; persistent multi-turn history is not included in this fix.
- Expense extraction currently expects an explicit UAH amount.
- Broader task and reminder execution requires separate tool-routing contracts.

The machine-readable evidence is in [bug-reproducer-evidence.json](<C:/Users/Денис/Desktop/Всякое вайбкодинг/Просто тест всякое/FAMILY-AI-LIFE-OS/outputs/bug-reproducer-evidence.json>).
