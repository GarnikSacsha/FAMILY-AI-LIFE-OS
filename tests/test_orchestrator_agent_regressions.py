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
async def test_general_message_uses_reasoning_provider() -> None:
    expected_response = "Да, вижу, настройка заняла немало времени. Теперь я на связи."

    with patch.dict("sys.modules", _stub_google_genai()):
        from app.integrations.llm.provider import TerraReasoningProvider

        with patch.object(
            TerraReasoningProvider,
            "generate_text",
            new=AsyncMock(return_value=expected_response),
        ) as generate_text:
            response = await MainOrchestrator.process_user_message(
                session=object(),
                user_id=uuid.uuid4(),
                household_id=uuid.uuid4(),
                user_name="Denys",
                message_text="Ты знаешь, как долго я тебя настраивал?",
            )

    assert response == expected_response
    generate_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_natural_language_expense_is_logged() -> None:
    household_id = uuid.uuid4()
    transaction_result = {
        "transaction_id": str(uuid.uuid4()),
        "amount": "1900.00",
        "currency": "UAH",
        "merchant": "базар",
        "category": "Groceries",
        "status": "SUCCESS",
    }

    with patch.dict("sys.modules", _stub_google_genai()):
        from app.agents.finance.agent import FinanceAgent

        with patch.object(
            FinanceAgent,
            "categorize_and_log_transaction",
            new=AsyncMock(return_value=transaction_result),
        ) as log_transaction:
            response = await MainOrchestrator.process_user_message(
                session=object(),
                user_id=uuid.uuid4(),
                household_id=household_id,
                user_name="Denys",
                message_text="Запиши пожалуйста мои траты сегодняшние, базар 1900 грн",
            )

    log_transaction.assert_awaited_once()
    call = log_transaction.await_args
    assert call.kwargs["owner_type"] == "household"
    assert call.kwargs["owner_id"] == household_id
    assert call.kwargs["amount"] == 1900
    assert call.kwargs["merchant"] == "базар"
    assert "1900" in response
    assert "UAH" in response
