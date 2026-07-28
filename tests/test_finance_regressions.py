import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.finance.agent import FinanceAgent
from app.orchestration.orchestrator import MainOrchestrator
from app.tools.finance_tools import FinanceTools


@pytest.mark.asyncio
async def test_orchestrator_formats_current_multicurrency_summary_contract():
    household_id = uuid.uuid4()
    summary = {
        "currencies": {
            "UAH": {
                "total_expense": "125.50",
                "categories": {"Groceries": "125.50"},
            },
            "EUR": {
                "total_expense": "10.00",
                "categories": {"Travel": "10.00"},
            },
        }
    }

    with patch(
        "app.orchestration.orchestrator.FinanceTools.get_spending_summary",
        new=AsyncMock(return_value=summary),
    ) as get_summary:
        response = await MainOrchestrator.process_user_message(
            session=object(),
            user_id=uuid.uuid4(),
            household_id=household_id,
            user_name="Denys",
            message_text="Сколько расходов за этот месяц?",
        )

    kwargs = get_summary.await_args.kwargs
    assert kwargs["owner_id"] == household_id
    assert kwargs["date_from"] < kwargs["date_to"]
    assert "125.50 UAH" in response
    assert "10.00 EUR" in response
    assert "Продукты" in response


def test_current_month_bounds_respect_kyiv_calendar():
    date_from, date_to = MainOrchestrator.current_month_bounds(
        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc),
        timezone_name="Europe/Kyiv",
    )

    assert date_from == datetime(2026, 6, 30, 21, tzinfo=timezone.utc)
    assert date_to == datetime(2026, 7, 31, 21, tzinfo=timezone.utc)


def test_today_spending_bounds_respect_kyiv_calendar():
    date_from, date_to, label = MainOrchestrator.spending_period(
        "Братишка, сколько мы потратили сегодня?",
        now=datetime(2026, 7, 27, 12, tzinfo=timezone.utc),
        timezone_name="Europe/Kyiv",
    )

    assert label == "сегодня"
    assert date_from == datetime(2026, 7, 26, 21, tzinfo=timezone.utc)
    assert date_to == datetime(2026, 7, 27, 21, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_today_spending_query_is_friendly_and_uses_daily_bounds():
    household_id = uuid.uuid4()
    with patch(
        "app.orchestration.orchestrator.FinanceTools.get_spending_summary",
        new=AsyncMock(
            return_value={
                "currencies": {
                    "UAH": {
                        "total_expense": "250.00",
                        "categories": {"Entertainment": "250.00"},
                    }
                }
            }
        ),
    ) as get_summary:
        response = await MainOrchestrator.process_user_message(
            session=object(),
            user_id=uuid.uuid4(),
            household_id=household_id,
            user_name="Denys",
            message_text="Братишка, сколько лавэ мы потратили сегодня?",
        )

    assert "Братишка" in response
    assert "сегодня" in response
    assert "Развлечения" in response
    assert get_summary.await_args.kwargs["date_to"] - get_summary.await_args.kwargs["date_from"] == timedelta(days=1)


def test_clear_family_expenses_have_deterministic_fallback_categories() -> None:
    assert FinanceAgent._fallback_category("367 грн отдых", "отдых") == "Entertainment"
    assert FinanceAgent._fallback_category("140 грн булка корм", "булка корм") == "Pets"


@pytest.mark.asyncio
async def test_explicit_coffee_expense_reaches_transaction_log() -> None:
    household_id = uuid.uuid4()
    transaction = {
        "transaction_id": str(uuid.uuid4()),
        "amount": "95.00",
        "currency": "UAH",
        "merchant": "Кофе",
        "category": "Restaurants",
        "status": "SUCCESS",
    }
    with patch.object(
        FinanceAgent,
        "categorize_and_log_transaction",
        new=AsyncMock(return_value=transaction),
    ) as log_transaction:
        response = await MainOrchestrator.process_user_message(
            session=object(),
            user_id=uuid.uuid4(),
            household_id=household_id,
            user_name="Саша",
            message_text="Кофе 95 грн",
        )

    assert log_transaction.await_args.kwargs["owner_id"] == household_id
    assert log_transaction.await_args.kwargs["amount"] == 95
    assert log_transaction.await_args.kwargs["merchant"].casefold() == "кофе"
    assert "95.00" in response


@pytest.mark.asyncio
async def test_today_coffee_followup_reads_postgres_not_general_llm() -> None:
    household_id = uuid.uuid4()
    find_transactions = AsyncMock(
        return_value=[
            {
                "amount": "95.00",
                "currency": "UAH",
                "merchant": "Кофе",
                "category": "Restaurants",
            }
        ]
    )
    with (
        patch.object(
            FinanceTools,
            "find_expenses",
            new=find_transactions,
        ),
        patch(
            "app.integrations.llm.provider.TerraReasoningProvider.generate_text",
            new=AsyncMock(return_value="Если появится доступ к учёту, внесём кофе."),
        ) as general_llm,
    ):
        response = await MainOrchestrator.process_user_message(
            session=object(),
            user_id=uuid.uuid4(),
            household_id=household_id,
            user_name="Саша",
            message_text="А сегодняшний кофе?",
        )

    find_transactions.assert_awaited_once()
    general_llm.assert_not_awaited()
    assert "95.00 UAH" in response
    assert "Кофе" in response
