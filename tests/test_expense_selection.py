import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domains.finance.models import FinancialTransaction
from app.domains.identity.models import Household, User
from app.domains.memory.models import PendingConfirmation, PendingSharedAction
from app.infrastructure.database.base import Base
from app.integrations.google.sheets import MONTHLY_BUDGET_HEADERS, GoogleSheetsClient
from app.orchestration.orchestrator import MainOrchestrator
from app.tools.expense_selection import expense_timestamp, select_expense_category


@pytest.mark.parametrize("text, day", [
    ("200 грн продукты 07.09", 7), ("760 грн", 8), ("7.50 грн", 8),
    ("90 грн - другое 07.09.2026", 7), ("300 грн вчера", 7),
])
def test_expense_dates(text, day):
    stamp = expense_timestamp(text, "Europe/Kyiv", now=datetime(2026, 9, 8, 10, tzinfo=timezone.utc))
    assert stamp.day == day
    assert stamp.month == 9


def test_invalid_date_rejected():
    with pytest.raises(ValueError):
        expense_timestamp("760 грн 31.02", "Europe/Kyiv")


@pytest.mark.parametrize("category", MONTHLY_BUDGET_HEADERS)
def test_selected_header_projects_to_same_column(category):
    assert GoogleSheetsClient.monthly_budget_category(category) == category


@pytest.mark.asyncio
async def test_selection_persists_exact_category_date_and_is_scoped_and_single_use():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        Household.__table__, User.__table__, PendingConfirmation.__table__,
        FinancialTransaction.__table__, PendingSharedAction.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=tables)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    household, user, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with factory.begin() as session:
        session.add(Household(id=household, name="Family", timezone="Europe/Kyiv"))
        session.add_all([
            User(id=user, household_id=household, telegram_id=1, first_name="One"),
            User(id=other, household_id=household, telegram_id=2, first_name="Two"),
        ])
    async with factory.begin() as session:
        response = await MainOrchestrator.process_user_message(
            session=session, user_id=user, household_id=household, user_name="One",
            message_text="200 грн продукты 07.09\n90 грн другое 06.09", telegram_chat_id=-100,
            telegram_message_id=99, pending_actions_enabled=True,
        )
        assert "Выберите категорию" in response
        assert "07.09" in response
        assert not (await session.execute(select(FinancialTransaction))).scalars().all()
        pending = (await session.execute(select(PendingConfirmation))).scalar_one()
        code = pending.confirmation_code
        reply = await MainOrchestrator._handle_confirmation_reply(
            session, user_id=user, household_id=household, telegram_chat_id=-100,
            message_text=f"подтвердить {code}",
        )
        assert "Выберите категорию" in reply
        assert not (await session.execute(select(FinancialTransaction))).scalars().all()

    async def choose(*, actor=user, chat=-100, index=0, category=7):
        async with factory.begin() as session:
            return await select_expense_category(
                session, user_id=actor, household_id=household, telegram_chat_id=chat,
                code=code, index=index, category_index=category,
            )

    assert "Не нашёл" in await choose(actor=other)
    assert "Не нашёл" in await choose(chat=-200)
    assert "Неизвестная" in await choose(category=10)
    first = await choose()
    assert "Быт" in first and "расход 2 из 2" in first
    assert "уже использована" in await choose()
    assert "Расход сохранён" in await choose(index=1, category=9)
    assert "уже обработан" in await choose(index=1)
    async with factory() as session:
        rows = (await session.scalars(
            select(FinancialTransaction).order_by(FinancialTransaction.amount.desc())
        )).all()
        assert len(rows) == 2
        assert [(r.category, r.occurred_at.day) for r in rows] == [("Быт", 7), ("Другое", 6)]
    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_selection_cannot_write():
    from unittest.mock import AsyncMock, patch

    pending = PendingConfirmation(
        status="pending", action_type="finance_log", expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        payload={"category_selection": True},
    )
    with patch("app.tools.expense_selection.ConfirmationTools.find_for_reply", AsyncMock(return_value=pending)):
        response = await select_expense_category(
            AsyncMock(), user_id=uuid.uuid4(), household_id=uuid.uuid4(),
            telegram_chat_id=-100, code="abcdef", index=0, category_index=0,
        )
    assert "Срок выбора истёк" in response
