import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domains.planning.models import Reminder
from app.domains.planning.reminder_parser import parse_reminder_request
from app.infrastructure.database.base import Base
from app.infrastructure.integrations.reminder_worker import deliver_due_reminders
from app.orchestration.orchestrator import MainOrchestrator


def test_parses_single_reminder_in_user_timezone() -> None:
    parsed = parse_reminder_request(
        "Напомни завтра в 10:30 позвонить врачу",
        timezone_name="Europe/Kyiv",
        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc),
    )

    assert parsed is not None
    assert parsed.title == "позвонить врачу"
    assert parsed.trigger_times == (datetime(2026, 7, 27, 7, 30, tzinfo=timezone.utc),)


def test_screenshot_phrase_creates_three_reminders_during_the_week() -> None:
    parsed = parse_reminder_request(
        (
            "Во-вторых. Нам надо решить с отпуском на 7-8-9 и возможно поход в горы, "
            "1-2 августа, так шо напоминай тоже в течении недели об этом"
        ),
        timezone_name="Europe/Kyiv",
        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc),
    )

    assert parsed is not None
    assert parsed.title.startswith("Нам надо решить с отпуском")
    assert [value.astimezone(ZoneInfo("Europe/Kyiv")).strftime("%d.%m %H:%M") for value in parsed.trigger_times] == [
        "27.07 10:00",
        "29.07 10:00",
        "01.08 10:00",
    ]


def test_parses_common_short_relative_phrase() -> None:
    now = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
    parsed = parse_reminder_request(
        "Напомни через полчаса проверить духовку",
        timezone_name="Europe/Kyiv",
        now=now,
    )

    assert parsed is not None
    assert parsed.title == "проверить духовку"
    assert parsed.trigger_times == (now + timedelta(minutes=30),)


@pytest.mark.asyncio
async def test_orchestrator_persists_reminder_for_source_chat() -> None:
    captured: list[Reminder] = []
    session = SimpleNamespace(
        add=captured.append,
        flush=AsyncMock(),
    )

    with patch(
        "app.orchestration.orchestrator.datetime",
        wraps=datetime,
    ):
        response = await MainOrchestrator.process_user_message(
            session=session,
            user_id=uuid.uuid4(),
            household_id=uuid.uuid4(),
            user_name="Denys",
            message_text="Напомни через 30 минут проверить духовку",
            telegram_chat_id=-100123456,
        )

    assert len(captured) == 1
    assert captured[0].telegram_chat_id == -100123456
    assert captured[0].title == "проверить духовку"
    assert "Пришлю прямо в этот чат" in response


@pytest.mark.asyncio
async def test_worker_delivers_and_marks_due_reminder() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=[Reminder.__table__])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    reminder_id = uuid.uuid4()
    recipient_id = uuid.uuid4()
    due_at = datetime.now(timezone.utc) - timedelta(minutes=1)

    async with factory.begin() as session:
        session.add(
            Reminder(
                id=reminder_id,
                recipient_id=recipient_id,
                telegram_chat_id=12345,
                title="проверить духовку",
                trigger_at=due_at,
            )
        )

    bot = AsyncMock()
    with patch(
        "app.infrastructure.integrations.reminder_worker.AsyncSessionLocal",
        new=factory,
    ):
        delivered = await deliver_due_reminders(bot)

    assert delivered == 1
    bot.send_message.assert_awaited_once_with(
        chat_id=12345,
        text="🔔 Напоминание\n\nпроверить духовку",
    )
    async with factory() as session:
        reminder = (await session.execute(select(Reminder).where(Reminder.id == reminder_id))).scalar_one()
        assert reminder.is_triggered is True
        assert reminder.delivery_status == "delivered"
        assert reminder.delivered_at is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_worker_retries_after_temporary_telegram_failure() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=[Reminder.__table__])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    reminder_id = uuid.uuid4()

    async with factory.begin() as session:
        session.add(
            Reminder(
                id=reminder_id,
                recipient_id=uuid.uuid4(),
                telegram_chat_id=12345,
                title="позвонить врачу",
                trigger_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
        )

    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("temporary Telegram failure")
    with patch(
        "app.infrastructure.integrations.reminder_worker.AsyncSessionLocal",
        new=factory,
    ):
        delivered = await deliver_due_reminders(bot)

    assert delivered == 0
    async with factory() as session:
        reminder = (await session.execute(select(Reminder).where(Reminder.id == reminder_id))).scalar_one()
        assert reminder.is_triggered is False
        assert reminder.delivery_status == "failed"
        assert reminder.delivery_attempts == 1
        assert reminder.next_attempt_at is not None
        assert reminder.last_error == "RuntimeError"

    await engine.dispose()
