import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.domains.identity.service import ActorContext
from app.domains.memory.models import PendingSharedAction
from app.domains.planning.semantic_calendar import CalendarIntentInterpreter, is_day_or_birthday_request
from app.orchestration.orchestrator import MainOrchestrator
from app.telegram.bot import handle_user_message
from app.tools.google_tools import GoogleWorkspaceTools
from tests.test_shared_memory import _memory_database, _seed_family
from tests.test_telegram_security import Message


@pytest.mark.parametrize(
    "message",
    ["Запишем, пожалуйста, 27 числа Настя ДР.", "Запиши на 27 число «др Насти»"],
)
def test_birthday_request_reaches_calendar_authorization(message: str) -> None:
    assert MainOrchestrator.domain_for_message(message) == "calendar"


@pytest.mark.asyncio
async def test_birthday_request_saves_draft_and_acknowledgement_keeps_it() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    event_date = (datetime.now(ZoneInfo("Europe/Kyiv")) + timedelta(days=8)).date()
    generate = AsyncMock(
        side_effect=[
            {
                "intent": "calendar",
                "title": "ДР Насти",
                "event_date": event_date.isoformat(),
                "confidence": 0.99,
                "is_new_request": is_new,
            }
            for is_new in (True, False)
        ]
    )
    interpreter = CalendarIntentInterpreter(provider=SimpleNamespace(generate_structured_json=generate))
    try:
        with (
            patch("app.orchestration.orchestrator.CalendarIntentInterpreter", return_value=interpreter),
            patch.object(
                MainOrchestrator, "_generate_general_response", new=AsyncMock(return_value="GENERAL")
            ) as general,
            patch.object(GoogleWorkspaceTools, "create_calendar_event", new=AsyncMock()) as create_event,
        ):
            for message in ("Запиши на 27 число «др Насти»", "Да"):
                async with factory.begin() as session:
                    response = await MainOrchestrator.process_user_message(
                        session=session,
                        user_id=user_id,
                        household_id=household_id,
                        user_name="Test",
                        message_text=message,
                        telegram_chat_id=123456789,
                        pending_actions_enabled=True,
                    )
                assert "На какое время" in response
                async with factory() as session:
                    action = (
                        await session.execute(
                            select(PendingSharedAction).where(PendingSharedAction.status == "pending")
                        )
                    ).scalar_one()
                    assert action.status == "pending"
                    assert action.payload["title"] == "ДР Насти"
                    assert action.payload["event_date"] == event_date.isoformat()
            general.assert_not_awaited()
            create_event.assert_not_awaited()
            assert generate.await_count == 2
    finally:
        await engine.dispose()


@pytest.mark.parametrize("message", ["Запиши 27 продуктов", "У Насти ДР", "27 числа свободен?", "Запиши 32 числа"])
def test_ordinary_numbers_and_conversation_are_not_calendar_commands(message: str) -> None:
    assert not is_day_or_birthday_request(message)


@pytest.mark.asyncio
async def test_family_group_gets_calendar_private_chat_instruction() -> None:
    message = Message("Запиши на 27 число «др Насти»", chat_id=-100123, chat_type="group")
    actor = ActorContext(
        user_id=uuid.uuid4(),
        telegram_id=message.from_user.id,
        household_id=uuid.uuid4(),
        chat_id=message.chat.id,
        chat_type="group",
    )

    @asynccontextmanager
    async def fake_uow():
        yield object()

    with (
        patch("app.telegram.bot.unit_of_work", new=fake_uow),
        patch("app.telegram.bot.IdentityService.resolve_actor", new=AsyncMock(return_value=actor)),
        patch.object(MainOrchestrator, "process_user_message", new=AsyncMock()) as process,
    ):
        await handle_user_message(message)
    process.assert_not_awaited()
    assert "в личном чате" in message.answers[-1][0]
    assert "Событие пока не создано" in message.answers[-1][0]
