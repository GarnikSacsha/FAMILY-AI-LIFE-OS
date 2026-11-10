from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.domains.memory.models import PendingSharedAction
from app.orchestration.orchestrator import MainOrchestrator
from app.orchestration.router import IntentRouter
from app.tools.google_tools import GoogleWorkspaceTools
from app.tools.memory_tools import SharedMemoryTools
from tests.test_shared_memory import _memory_database, _seed_family

RECURRING_LEARNING_REQUEST = (
    "А можешь мне, пожалуйста, добавить на каждый день, 16:00 каждый день, "
    "начиная с сегодняшнего дня, Learning Python. Запиши, чтобы я не забыл."
)

BOUNDED_RECURRING_REQUEST = (
    "Так, запиши мне, пожалуйста, Learning Python на каждый день. "
    "Вот чтобы типа с сегодняшнего дня по четверг у меня было в календаре написано Learning Python. "
    "Давай время поставь 16.00."
)


def test_recurring_learning_request_is_routed_to_planning() -> None:
    routing = IntentRouter.classify_intent(RECURRING_LEARNING_REQUEST)

    assert routing["intent"] == "PLANNING_OR_REMINDER"


@pytest.mark.asyncio
async def test_recurring_learning_request_persists_calendar_followup() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    chat_id = -100123

    async with factory.begin() as session:
        response = await MainOrchestrator.process_user_message(
            session=session,
            user_id=user_id,
            household_id=household_id,
            user_name="Саша",
            message_text=RECURRING_LEARNING_REQUEST,
            telegram_chat_id=chat_id,
            shared_context_enabled=True,
        )

    async with factory() as session:
        action = (
            await session.execute(
                select(PendingSharedAction).where(
                    PendingSharedAction.household_id == household_id,
                    PendingSharedAction.telegram_chat_id == chat_id,
                    PendingSharedAction.initiated_by_user_id == user_id,
                    PendingSharedAction.status == "pending",
                )
            )
        ).scalar_one()

    assert action.action_type == "calendar_recurring"
    assert action.payload["title"] == "Learning Python"
    assert action.payload["time"] == "16:00"
    assert "бессрочно" in response

    with patch.object(
        GoogleWorkspaceTools,
        "create_calendar_event",
        new=AsyncMock(return_value={"id": "event-1", "summary": "Learning Python"}),
    ) as create_event:
        async with factory.begin() as session:
            followup_response = await MainOrchestrator.process_user_message(
                session=session,
                user_id=user_id,
                household_id=household_id,
                user_name="Саша",
                message_text="Бессрочно",
                telegram_chat_id=chat_id,
                shared_context_enabled=True,
            )

    async with factory() as session:
        action = (
            await session.execute(
                select(PendingSharedAction).where(
                    PendingSharedAction.household_id == household_id,
                    PendingSharedAction.telegram_chat_id == chat_id,
                    PendingSharedAction.initiated_by_user_id == user_id,
                )
            )
        ).scalar_one()

    assert action.status == "completed"
    assert "бессрочно" in followup_response
    assert create_event.await_args.kwargs["recurrence"] == ["RRULE:FREQ=DAILY"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_private_chat_calendar_followup_uses_pending_action() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    private_chat_id = 123456789

    async with factory.begin() as session:
        response = await MainOrchestrator.process_user_message(
            session=session,
            user_id=user_id,
            household_id=household_id,
            user_name="Саша",
            message_text=RECURRING_LEARNING_REQUEST,
            telegram_chat_id=private_chat_id,
            shared_context_enabled=False,
            pending_actions_enabled=True,
        )

    assert "бессрочно" in response

    with (
        patch.object(
            GoogleWorkspaceTools,
            "create_calendar_event",
            new=AsyncMock(return_value={"id": "event-private", "summary": "Learning Python"}),
        ) as create_event,
        patch.object(
            MainOrchestrator,
            "_generate_general_response",
            new=AsyncMock(return_value="FALLBACK"),
        ) as general_response,
    ):
        async with factory.begin() as session:
            followup_response = await MainOrchestrator.process_user_message(
                session=session,
                user_id=user_id,
                household_id=household_id,
                user_name="Саша",
                message_text="Бессрочно",
                telegram_chat_id=private_chat_id,
                shared_context_enabled=False,
                pending_actions_enabled=True,
            )

    async with factory() as session:
        action = (
            await session.execute(
                select(PendingSharedAction).where(
                    PendingSharedAction.household_id == household_id,
                    PendingSharedAction.telegram_chat_id == private_chat_id,
                    PendingSharedAction.initiated_by_user_id == user_id,
                )
            )
        ).scalar_one()

    assert followup_response != "FALLBACK"
    assert action.status == "completed"
    assert create_event.await_args.kwargs["summary"] == "Learning Python"
    assert create_event.await_args.kwargs["recurrence"] == ["RRULE:FREQ=DAILY"]
    general_response.assert_not_awaited()

    await engine.dispose()


@pytest.mark.asyncio
async def test_complete_bounded_recurring_request_is_created_without_followup() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)

    with (
        patch("app.orchestration.orchestrator.datetime", wraps=datetime) as clock,
        patch.object(
            GoogleWorkspaceTools,
            "create_calendar_event",
            new=AsyncMock(return_value={"id": "event-bounded", "summary": "Learning Python"}),
        ) as create_event,
    ):
        clock.now.return_value = datetime(2026, 8, 3, 8, tzinfo=timezone.utc)
        async with factory.begin() as session:
            response = await MainOrchestrator.process_user_message(
                session=session,
                user_id=user_id,
                household_id=household_id,
                user_name="Денис",
                message_text=BOUNDED_RECURRING_REQUEST,
                telegram_chat_id=123456789,
                pending_actions_enabled=True,
            )

    assert "добавил" in response.lower()
    assert create_event.await_args.kwargs["summary"] == "Learning Python"
    assert create_event.await_args.kwargs["start_at"] == datetime(
        2026,
        8,
        3,
        16,
        tzinfo=ZoneInfo("Europe/Kyiv"),
    )
    assert create_event.await_args.kwargs["recurrence"] == ["RRULE:FREQ=DAILY;UNTIL=20260806T130000Z"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_recurring_followup_accepts_time_and_end_date_together() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    initial_message = "Добавь в календарь каждый день, начиная с сегодняшнего дня, пойти к дедушке набрать воды"

    with (
        patch("app.orchestration.orchestrator.datetime", wraps=datetime) as clock,
        patch.object(
            GoogleWorkspaceTools,
            "create_calendar_event",
            new=AsyncMock(return_value={"id": "event-grandfather", "summary": "пойти к дедушке набрать воды"}),
        ) as create_event,
    ):
        clock.now.return_value = datetime(2026, 8, 3, 8, tzinfo=timezone.utc)
        async with factory.begin() as session:
            await MainOrchestrator.process_user_message(
                session=session,
                user_id=user_id,
                household_id=household_id,
                user_name="Денис",
                message_text=initial_message,
                telegram_chat_id=123456789,
                pending_actions_enabled=True,
            )

        async with factory.begin() as session:
            response = await MainOrchestrator.process_user_message(
                session=session,
                user_id=user_id,
                household_id=household_id,
                user_name="Денис",
                message_text="Поставить время 16:00 и продлить это до конца недели",
                telegram_chat_id=123456789,
                pending_actions_enabled=True,
            )

    async with factory() as session:
        action = (await session.execute(select(PendingSharedAction))).scalar_one()

    assert "добавил" in response.lower()
    assert action.status == "completed"
    assert create_event.await_args.kwargs["summary"] == "пойти к дедушке набрать воды"
    assert create_event.await_args.kwargs["recurrence"] == ["RRULE:FREQ=DAILY;UNTIL=20260809T130000Z"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_complete_quoted_calendar_request_supersedes_stale_pending_action() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    chat_id = 123456789

    async with factory.begin() as session:
        await SharedMemoryTools.create_pending_calendar_recurring(
            session,
            household_id=household_id,
            telegram_chat_id=chat_id,
            initiated_by_user_id=user_id,
            title="Так общем напиши мне пожалуйста",
            start_at=datetime(2026, 8, 3, 16, tzinfo=ZoneInfo("Europe/Kyiv")),
            timezone_name="Europe/Kyiv",
        )

    with (
        patch("app.orchestration.orchestrator.datetime", wraps=datetime) as clock,
        patch.object(
            GoogleWorkspaceTools,
            "create_calendar_event",
            new=AsyncMock(return_value={"id": "event-python", "summary": "изучение python"}),
        ) as create_event,
    ):
        clock.now.return_value = datetime(2026, 8, 3, 8, tzinfo=timezone.utc)
        async with factory.begin() as session:
            await MainOrchestrator.process_user_message(
                session=session,
                user_id=user_id,
                household_id=household_id,
                user_name="Денис",
                message_text=('Добавь в календарь с сегодняшнего дня до четверга на 17:00 😊 "изучение python"'),
                telegram_chat_id=chat_id,
                pending_actions_enabled=True,
            )

    assert create_event.await_args.kwargs["summary"] == "изучение python"
    assert create_event.await_args.kwargs["start_at"] == datetime(
        2026,
        8,
        3,
        17,
        tzinfo=ZoneInfo("Europe/Kyiv"),
    )
    assert create_event.await_args.kwargs["recurrence"] == ["RRULE:FREQ=DAILY;UNTIL=20260806T140000Z"]
    await engine.dispose()
