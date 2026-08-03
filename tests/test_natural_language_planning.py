from datetime import datetime
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


def test_haircut_calendar_request_uses_only_the_event_title() -> None:
    message = "Запиши меня, пожалуйста, на стрижку на завтра"

    assert IntentRouter.classify_intent(message)["intent"] == "PLANNING_OR_REMINDER"
    assert MainOrchestrator.domain_for_message(message) == "calendar"
    assert MainOrchestrator._calendar_title(message) == "стрижка"


def test_recurring_calendar_request_uses_only_the_explicit_learning_title() -> None:
    message = (
        "Так, можешь, пожалуйста, мне добавить на каждый день напоминание в календарь "
        "на 16:00, начиная с сегодняшнего дня. То есть название — Learning Python Coursera. Вот так"
    )

    assert MainOrchestrator._recurring_calendar_title(message) == "Learning Python Coursera"


@pytest.mark.asyncio
async def test_natural_reminder_sentence_persists_the_subject_for_followup() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)

    with patch.object(
        MainOrchestrator,
        "_generate_general_response",
        new=AsyncMock(return_value="Ожидаю дату и время."),
    ):
        async with factory.begin() as session:
            await MainOrchestrator.process_user_message(
                session=session,
                user_id=user_id,
                household_id=household_id,
                user_name="Саша",
                message_text="Сделай, пожалуйста, мне напоминание учить Python",
                telegram_chat_id=-100123,
                shared_context_enabled=True,
            )

    async with factory() as session:
        action = (await session.execute(select(PendingSharedAction))).scalar_one()

    assert action.action_type == "reminder"
    assert action.payload["title"] == "учить Python"
    await engine.dispose()


@pytest.mark.asyncio
async def test_haircut_request_keeps_date_until_time_followup_arrives() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)

    async with factory.begin() as session:
        response = await MainOrchestrator.process_user_message(
            session=session,
            user_id=user_id,
            household_id=household_id,
            user_name="Саша",
            message_text="Запиши меня, пожалуйста, на стрижку на завтра",
            telegram_chat_id=-100123,
            shared_context_enabled=True,
        )

    async with factory() as session:
        action = (await session.execute(select(PendingSharedAction))).scalar_one()

    assert action.action_type == "calendar_event"
    assert action.payload["title"] == "стрижка"
    assert "На какое время" in response

    with patch.object(
        GoogleWorkspaceTools,
        "create_calendar_event",
        new=AsyncMock(return_value={"id": "event-2", "summary": "стрижка"}),
    ) as create_event:
        async with factory.begin() as session:
            followup_response = await MainOrchestrator.process_user_message(
                session=session,
                user_id=user_id,
                household_id=household_id,
                user_name="Саша",
                message_text="В 15:00",
                telegram_chat_id=-100123,
                shared_context_enabled=True,
            )

    assert "стрижка" in followup_response
    assert create_event.await_args.kwargs["summary"] == "стрижка"
    assert create_event.await_args.kwargs["start_at"].hour == 15
    await engine.dispose()


@pytest.mark.asyncio
async def test_calendar_title_correction_updates_pending_action_without_losing_schedule() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    start_at = datetime(2026, 8, 3, 16, tzinfo=ZoneInfo("Europe/Kyiv"))

    async with factory.begin() as session:
        await SharedMemoryTools.create_pending_calendar_recurring(
            session,
            household_id=household_id,
            telegram_chat_id=-100123,
            initiated_by_user_id=user_id,
            title="служебный текст",
            start_at=start_at,
            timezone_name="Europe/Kyiv",
        )

    async with factory.begin() as session:
        await MainOrchestrator.process_user_message(
            session=session,
            user_id=user_id,
            household_id=household_id,
            user_name="Саша",
            message_text="Название — Learning Python Coursera",
            telegram_chat_id=-100123,
            shared_context_enabled=True,
        )

    async with factory() as session:
        action = (await session.execute(select(PendingSharedAction))).scalar_one()

    assert action.status == "pending"
    assert action.payload["title"] == "Learning Python Coursera"
    assert action.payload["start_at"] == start_at.astimezone(ZoneInfo("UTC")).isoformat()
    await engine.dispose()
