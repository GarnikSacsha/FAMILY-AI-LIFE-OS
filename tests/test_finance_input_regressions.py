import uuid
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest

from app.orchestration.orchestrator import MainOrchestrator


def _stub_google_genai() -> dict[str, ModuleType]:
    google = ModuleType("google")
    genai = ModuleType("google.genai")
    genai_types = ModuleType("google.genai.types")
    genai.types = genai_types
    google.genai = genai
    return {
        "google": google,
        "google.genai": genai,
        "google.genai.types": genai_types,
    }


@pytest.mark.asyncio
async def test_replayed_manual_expense_uses_stable_telegram_identity() -> None:
    household_id = uuid.uuid4()
    transaction_result = {
        "transaction_id": str(uuid.uuid4()),
        "amount": "3900.00",
        "currency": "UAH",
        "merchant": "\u0432\u0456\u0434\u043f\u043e\u0447\u0438\u043d\u043e\u043a",
        "category": "Entertainment",
        "status": "SUCCESS",
    }
    message = "3900 \u0433\u0440\u043d \u0432\u0456\u0434\u043f\u043e\u0447\u0438\u043d\u043e\u043a"

    with patch.dict("sys.modules", _stub_google_genai()):
        from app.agents.finance.agent import FinanceAgent

        with patch.object(
            FinanceAgent,
            "categorize_and_log_transaction",
            new=AsyncMock(return_value=transaction_result),
        ) as log_transaction:
            for _ in range(2):
                await MainOrchestrator.process_user_message(
                    session=object(),
                    user_id=uuid.uuid4(),
                    household_id=household_id,
                    user_name="Denys",
                    message_text=message,
                    telegram_chat_id=-100123,
                    telegram_message_id=456,
                )

    assert log_transaction.await_count == 2
    external_ids = [call.kwargs["external_id"] for call in log_transaction.await_args_list]
    assert external_ids == [
        "telegram:-100123:456:expense:1",
        "telegram:-100123:456:expense:1",
    ]
