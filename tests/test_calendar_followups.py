from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.domains.memory.models import PendingSharedAction
from app.orchestration.orchestrator import MainOrchestrator
from app.orchestration.router import IntentRouter
from app.tools.google_tools import GoogleWorkspaceTools
from tests.test_shared_memory import _memory_database, _seed_family

RECURRING_LEARNING_REQUEST = (
    "А можешь мне, пожалуйста, добавить на каждый день, 16:00 каждый день, "
    "начиная с сегодняшнего дня, Learning Python. Запиши, чтобы я не забыл."
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
