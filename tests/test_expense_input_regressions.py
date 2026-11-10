import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.orchestration.orchestrator import MainOrchestrator
from app.tools.expense_selection import expense_timestamp


@pytest.mark.parametrize(
    "text, expected",
    [
        ("I spent 350 UAH on groceries today.", [(350.0, "groceries")]),
        ("I spent 1,350 UAH on groceries yesterday.", [(1350.0, "groceries")]),
        ("1,350.50 UAH groceries", [(1350.5, "groceries")]),
        ("1 350 грн продукты", [(1350.0, "продукты")]),
        ("350,50 грн продукты", [(350.5, "продукты")]),
        ("350 грн продукты", [(350.0, "продукты")]),
    ],
)
def test_expense_labels_and_grouped_amounts(text, expected):
    assert MainOrchestrator._extract_expenses(text) == expected


@pytest.mark.parametrize(
    "text", ["-350 UAH groceries", "−350 UAH groceries", "-1,350 UAH groceries", "350 грн продукты\n-20 такси"]
)
@pytest.mark.asyncio
async def test_negative_amount_has_explicit_error_without_writes(text):
    session = AsyncMock()
    result = await MainOrchestrator.process_user_message(
        session,
        uuid.uuid4(),
        uuid.uuid4(),
        "Demo",
        text,
        pending_actions_enabled=True,
        telegram_chat_id=1,
        telegram_message_id=1,
    )
    assert "negative" in result.lower()
    session.execute.assert_not_awaited()
    session.flush.assert_not_awaited()


@pytest.mark.parametrize("suffix, expected", [("today", 16), ("yesterday", 15), ("day before yesterday", 14)])
def test_english_expense_dates(suffix, expected):
    now = datetime(2026, 10, 16, 10, tzinfo=timezone.utc)
    assert expense_timestamp(f"350 UAH groceries {suffix}", "Europe/Kyiv", now=now).day == expected


@pytest.mark.parametrize("suffix", ["last week", "tomorrow", "three days ago", "on Monday", "2026-10-01"])
def test_unsupported_expense_date_is_explicitly_rejected(suffix):
    with pytest.raises(ValueError):
        expense_timestamp(f"350 UAH groceries {suffix}", "Europe/Kyiv")


@pytest.mark.asyncio
async def test_expense_does_not_invoke_semantic_calendar():
    from tests.test_expense_selection import test_selection_persists_exact_category_date_and_is_scoped_and_single_use

    with patch("app.orchestration.orchestrator.CalendarIntentInterpreter.interpret", AsyncMock()) as interpret:
        await test_selection_persists_exact_category_date_and_is_scoped_and_single_use()
    interpret.assert_not_awaited()


def test_sqlite_utc_timestamp_is_not_reinterpreted_in_machine_timezone():
    from app.infrastructure.integrations.google_sheets_worker import TransactionSyncItem

    item = TransactionSyncItem(
        id=uuid.uuid4(),
        occurred_at=datetime(2026, 10, 15, 9),
        merchant="groceries",
        category="Groceries",
        amount="350",
        currency="UAH",
        direction="expense",
        source="manual",
        owner_type="household",
    )
    assert item.as_sheet_row()[0] == "2026-10-15T09:00:00+00:00"


@pytest.mark.asyncio
async def test_english_grouped_expense_persists_yesterday_and_selected_category():
    from decimal import Decimal

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.domains.finance.models import FinancialTransaction
    from app.domains.identity.models import Household, User
    from app.domains.memory.models import PendingConfirmation, PendingSharedAction
    from app.infrastructure.database.base import Base
    from app.tools.expense_selection import select_expense_category

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        model.__table__
        for model in (
            Household,
            User,
            PendingConfirmation,
            PendingSharedAction,
            FinancialTransaction,
        )
    ]
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=tables)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    household_id, user_id = uuid.uuid4(), uuid.uuid4()
    text = "I spent 1,350 UAH on groceries yesterday."
    expected_date = expense_timestamp(text, "Europe/Kyiv").date()
    try:
        async with factory.begin() as session:
            session.add(Household(id=household_id, name="Demo Household"))
            session.add(User(id=user_id, household_id=household_id, telegram_id=100000001, first_name="Demo User"))
        async with factory.begin() as session:
            response = await MainOrchestrator.process_user_message(
                session,
                user_id,
                household_id,
                "Demo User",
                text,
                telegram_chat_id=100000001,
                telegram_message_id=1,
                pending_actions_enabled=True,
            )
            assert "1350" in response
            pending = (await session.scalars(select(PendingConfirmation))).one()
            assert not (await session.scalars(select(FinancialTransaction))).all()
            code = pending.confirmation_code
        async with factory.begin() as session:
            await select_expense_category(
                session,
                user_id=user_id,
                household_id=household_id,
                telegram_chat_id=100000001,
                code=code,
                index=0,
                category_index=0,
            )
        async with factory() as session:
            row = (await session.scalars(select(FinancialTransaction))).one()
            assert row.amount == Decimal("1350.00")
            assert row.merchant == "groceries"
            assert row.category == "Продукты"
            assert row.occurred_at.date() == expected_date
            assert row.sheets_sync_status == "pending"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unsupported_date_rejected_before_database_access():
    session = AsyncMock()
    response = await MainOrchestrator.process_user_message(
        session,
        uuid.uuid4(),
        uuid.uuid4(),
        "Demo",
        "I spent 350 UAH on groceries last week",
        pending_actions_enabled=True,
        telegram_chat_id=1,
    )
    assert "Unsupported expense date" in response
    session.execute.assert_not_awaited()
