import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.orchestration.orchestrator import MainOrchestrator


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
