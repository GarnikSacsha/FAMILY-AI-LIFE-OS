import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domains.identity.models import Household, User
from app.domains.memory.models import PendingConfirmation, PendingSharedAction, SharedMemoryItem
from app.infrastructure.database.base import Base
from app.orchestration.orchestrator import MainOrchestrator
from app.tools.confirmation_tools import ConfirmationTools
from app.tools.memory_tools import SharedMemoryTools

CONFIRMATION_TABLES = [
    Household.__table__,
    User.__table__,
    PendingConfirmation.__table__,
    PendingSharedAction.__table__,
    SharedMemoryItem.__table__,
]


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=CONFIRMATION_TABLES)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(factory):
    household_id = uuid.uuid4()
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    async with factory.begin() as session:
        session.add(Household(id=household_id, name="Family", timezone="Europe/Kyiv"))
        session.add(User(id=user_id, household_id=household_id, telegram_id=1, first_name="Денис"))
        session.add(User(id=other_user_id, household_id=household_id, telegram_id=2, first_name="Аня"))
    return household_id, user_id, other_user_id


@pytest.mark.asyncio
async def test_finance_confirmation_is_bound_to_chat_user_and_single_use() -> None:
    engine, factory = await _database()
    household_id, user_id, other_user_id = await _seed(factory)
    chat_id = -100123
    message = "3900 грн магазин"

    async with factory.begin() as session:
        initial = await MainOrchestrator.process_user_message(
            session=session,
            user_id=user_id,
            household_id=household_id,
            user_name="Денис",
            message_text=message,
            telegram_chat_id=chat_id,
            telegram_message_id=77,
            pending_actions_enabled=True,
        )
    async with factory() as session:
        confirmation = (await session.execute(select(PendingConfirmation))).scalar_one()
    assert "Подтвердите запись расхода" in initial
    assert confirmation.status == "pending"
    assert confirmation.payload["expenses"][0]["external_id"] == "telegram:-100123:77:expense:1"

    async with factory.begin() as session:
        wrong_user = await MainOrchestrator.process_user_message(
            session=session,
            user_id=other_user_id,
            household_id=household_id,
            user_name="Аня",
            message_text=f"подтвердить {confirmation.confirmation_code}",
            telegram_chat_id=chat_id,
            pending_actions_enabled=True,
        )
    assert "Не нашёл" in wrong_user

    transaction = {
        "status": "SUCCESS",
        "amount": "3900.00",
        "currency": "UAH",
        "merchant": "магазин",
        "category": "Shopping",
    }
    with patch(
        "app.agents.finance.agent.FinanceAgent.categorize_and_log_transaction", new=AsyncMock(return_value=transaction)
    ) as log:
        async with factory.begin() as session:
            confirmed = await MainOrchestrator.process_user_message(
                session=session,
                user_id=user_id,
                household_id=household_id,
                user_name="Денис",
                message_text=f"подтвердить {confirmation.confirmation_code}",
                telegram_chat_id=chat_id,
                pending_actions_enabled=True,
            )
        async with factory.begin() as session:
            replayed = await MainOrchestrator.process_user_message(
                session=session,
                user_id=user_id,
                household_id=household_id,
                user_name="Денис",
                message_text=f"подтвердить {confirmation.confirmation_code}",
                telegram_chat_id=chat_id,
                pending_actions_enabled=True,
            )
    assert "Расход сохранён" in confirmed
    assert "уже не ожидает" in replayed
    assert log.await_count == 1
    assert log.await_args.kwargs["external_id"] == "telegram:-100123:77:expense:1"
    async with factory() as session:
        confirmation = (await session.execute(select(PendingConfirmation))).scalar_one()
    assert confirmation.status == "completed"
    await engine.dispose()


@pytest.mark.asyncio
async def test_memory_dismiss_requires_explicit_confirmation_and_cancel_is_safe() -> None:
    engine, factory = await _database()
    household_id, user_id, _ = await _seed(factory)
    chat_id = -100123
    async with factory.begin() as session:
        await SharedMemoryTools.remember(
            session,
            household_id=household_id,
            kind="fact",
            content="корм лучше покупать заранее",
        )
        initial = await MainOrchestrator.process_user_message(
            session=session,
            user_id=user_id,
            household_id=household_id,
            user_name="Денис",
            message_text="Не учитывай больше корм заранее",
            telegram_chat_id=chat_id,
            telegram_message_id=78,
            shared_context_enabled=True,
        )
    async with factory() as session:
        memory = (await session.execute(select(SharedMemoryItem))).scalar_one()
        confirmation = (await session.execute(select(PendingConfirmation))).scalar_one()
    assert memory.status == "active"
    assert "Подтвердите удаление" in initial

    async with factory.begin() as session:
        cancelled = await MainOrchestrator.process_user_message(
            session=session,
            user_id=user_id,
            household_id=household_id,
            user_name="Денис",
            message_text=f"отмена {confirmation.confirmation_code}",
            telegram_chat_id=chat_id,
            shared_context_enabled=True,
        )
    assert "отменена" in cancelled
    async with factory() as session:
        memory = (await session.execute(select(SharedMemoryItem))).scalar_one()
        confirmation = (await session.execute(select(PendingConfirmation))).scalar_one()
    assert memory.status == "active"
    assert confirmation.status == "cancelled"
    await engine.dispose()


@pytest.mark.asyncio
async def test_calendar_delete_executes_only_the_snapshotted_event() -> None:
    engine, factory = await _database()
    household_id, user_id, _ = await _seed(factory)
    chat_id = -100123
    events = [{"id": "event-1", "summary": "стоматолог"}]
    with (
        patch(
            "app.orchestration.orchestrator.GoogleWorkspaceTools.list_upcoming_events",
            new=AsyncMock(return_value=events),
        ),
        patch("app.orchestration.orchestrator.GoogleWorkspaceTools.delete_calendar_event", new=AsyncMock()) as delete,
    ):
        async with factory.begin() as session:
            initial = await MainOrchestrator.process_user_message(
                session=session,
                user_id=user_id,
                household_id=household_id,
                user_name="Денис",
                message_text="удали из календаря стоматолог",
                telegram_chat_id=chat_id,
                telegram_message_id=79,
                pending_actions_enabled=True,
            )
        async with factory() as session:
            confirmation = (await session.execute(select(PendingConfirmation))).scalar_one()
        assert "Подтвердите удаление" in initial
        delete.assert_not_awaited()
        async with factory.begin() as session:
            confirmed = await MainOrchestrator.process_user_message(
                session=session,
                user_id=user_id,
                household_id=household_id,
                user_name="Денис",
                message_text=f"подтвердить {confirmation.confirmation_code}",
                telegram_chat_id=chat_id,
                pending_actions_enabled=True,
            )
    assert "Удалил событие" in confirmed
    assert delete.await_args.kwargs["event_id"] == "event-1"
    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_confirmation_cannot_be_claimed() -> None:
    engine, factory = await _database()
    household_id, user_id, _ = await _seed(factory)
    chat_id = -100123
    created_at = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    async with factory.begin() as session:
        confirmation = await ConfirmationTools.create_or_get(
            session,
            household_id=household_id,
            telegram_chat_id=chat_id,
            initiated_by_user_id=user_id,
            action_type="memory_dismiss",
            payload={"item_ids": []},
            request_key="telegram:-100123:80:memory_dismiss",
            now=created_at,
        )
    async with factory.begin() as session:
        expired = await ConfirmationTools.find_for_reply(
            session,
            household_id=household_id,
            telegram_chat_id=chat_id,
            initiated_by_user_id=user_id,
            confirmation_code=confirmation.confirmation_code,
            now=created_at + timedelta(minutes=16),
        )
        assert expired is not None
        assert not await ConfirmationTools.claim(session, expired, now=created_at + timedelta(minutes=16))
    async with factory() as session:
        persisted = (await session.execute(select(PendingConfirmation))).scalar_one()
    assert persisted.status == "expired"
    await engine.dispose()
