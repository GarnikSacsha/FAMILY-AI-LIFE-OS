import uuid
from types import ModuleType, SimpleNamespace
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


def test_ukrainian_expense_labels_use_expected_fallback_categories() -> None:
    from app.agents.finance.agent import FinanceAgent

    labels_and_categories = {
        "відпочинок": "Entertainment",
        "здоров'я": "Health",
        "продукти": "Groceries",
        "спорт": "Sports",
        "рахунки моб": "Utilities",
        "транспорт": "Transport",
    }

    for label, category in labels_and_categories.items():
        assert FinanceAgent._fallback_category(label, label) == category


@pytest.mark.asyncio
async def test_multiline_manual_expenses_are_logged_individually() -> None:
    household_id = uuid.uuid4()
    amounts = [3900, 185, 1750, 150, 750, 250]
    merchants = ["відпочинок", "здоров'я", "продукти", "спорт", "рахунки моб", "транспорт"]
    message = "\n".join(
        [
            "3900 грн відпочинок",
            "185 грн здоров'я",
            "1750 грн продукти",
            "150 грн спорт",
            "750 грн рахунки моб",
            "250 транспорт",
        ]
    )
    results = [
        {
            "transaction_id": str(uuid.uuid4()),
            "amount": f"{amount}.00",
            "currency": "UAH",
            "merchant": merchant,
            "category": "Uncategorized",
            "status": "SUCCESS",
        }
        for amount, merchant in zip(amounts, merchants, strict=True)
    ]

    with patch.dict("sys.modules", _stub_google_genai()):
        from app.agents.finance.agent import FinanceAgent

        with patch.object(
            FinanceAgent,
            "categorize_and_log_transaction",
            new=AsyncMock(side_effect=results),
        ) as log_transaction:
            await MainOrchestrator.process_user_message(
                session=object(),
                user_id=uuid.uuid4(),
                household_id=household_id,
                user_name="Denys",
                message_text=message,
            )

    assert [call.kwargs["amount"] for call in log_transaction.await_args_list] == amounts
    assert [call.kwargs["merchant"] for call in log_transaction.await_args_list] == merchants


@pytest.mark.asyncio
async def test_prefixed_multiline_expenses_create_one_confirmation_with_every_item() -> None:
    create_confirmation = AsyncMock(
        return_value=SimpleNamespace(
            status="pending",
            confirmation_code="FaBnUAG0",
        )
    )

    with (
        patch(
            "app.orchestration.orchestrator.ConfirmationTools.create_or_get",
            new=create_confirmation,
        ),
        patch(
            "app.orchestration.orchestrator.SharedMemoryTools.get_pending_action",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.orchestration.orchestrator.CalendarIntentInterpreter.interpret",
            new=AsyncMock(return_value=None),
        ),
    ):
        response = await MainOrchestrator.process_user_message(
            session=object(),
            user_id=uuid.uuid4(),
            household_id=uuid.uuid4(),
            user_name="Denys",
            message_text="Запиши 130 отдых\n64 грн продукты",
            telegram_chat_id=-100123,
            telegram_message_id=511,
            pending_actions_enabled=True,
        )

    create_confirmation.assert_awaited_once()
    assert create_confirmation.await_args.kwargs["payload"]["expenses"] == [
        {
            "amount": 130,
            "merchant": "отдых",
            "description": "отдых",
            "external_id": "telegram:-100123:511:expense:1",
        },
        {
            "amount": 64,
            "merchant": "продукты",
            "description": "продукты",
            "external_id": "telegram:-100123:511:expense:2",
        },
    ]
    assert "130 грн — отдых" in response
    assert "64 грн — продукты" in response
