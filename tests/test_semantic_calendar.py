from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.domains.memory.models import PendingSharedAction
from app.domains.planning.semantic_calendar import (
    CalendarIntentInterpreter,
    SemanticCalendarPlan,
    looks_like_planning_message,
)
from app.orchestration.orchestrator import MainOrchestrator
from app.tools.google_tools import GoogleWorkspaceTools
from tests.test_shared_memory import _memory_database, _seed_family


@pytest.mark.asyncio
async def test_interpreter_accepts_arbitrary_task_title_and_builds_context_prompt() -> None:
    generate = AsyncMock(
        return_value={
            "intent": "calendar",
            "is_new_request": True,
            "title": "Отвести собаку на стрижку",
            "event_date": "2026-08-04",
            "event_time": "09:00",
            "recurrence": "none",
            "recurrence_end_date": None,
            "recurring_forever": False,
            "confidence": 0.98,
        }
    )
    provider = SimpleNamespace(generate_structured_json=generate)

    plan = await CalendarIntentInterpreter(provider=provider).interpret(
        message_text="Запиши на завтра в девять утра отвести собаку на стрижку",
        local_now=datetime(2026, 8, 3, 12, tzinfo=ZoneInfo("Europe/Kyiv")),
        timezone_name="Europe/Kyiv",
    )

    assert plan is not None
    assert plan.title == "Отвести собаку на стрижку"
    assert plan.event_date == date(2026, 8, 4)
    assert plan.event_time == time(9, 0)
    prompt = generate.await_args.args[0]
    assert "Never require optional details" in prompt
    assert "Russian, Ukrainian, English" in prompt


@pytest.mark.parametrize(
    "message",
    [
        "Завтра таск купить корм собаке в 09:00",
        "Поставь на завтра в 11:30 интервью с рекрутером",
        "Добавь завтра в 18:00 пойти к дедушке набрать воды",
        "Запиши на завтра в 15:00 позвонить в банк",
        "Завтра о 10:00 купити квитки на концерт",
    ],
)
def test_planning_candidate_is_not_tied_to_one_task_name(message: str) -> None:
    assert looks_like_planning_message(message) is True


@pytest.mark.asyncio
async def test_semantic_calendar_followup_merges_draft_and_creates_google_event() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    chat_id = 123456789
    event_date = datetime.now(ZoneInfo("Europe/Kyiv")).date() + timedelta(days=1)
    plans = [
        SemanticCalendarPlan(
            intent="calendar",
            is_new_request=True,
            title="Купить билеты на концерт",
            event_date=event_date,
            event_time=None,
            recurrence="none",
            confidence=0.98,
        ),
        SemanticCalendarPlan(
            intent="calendar",
            is_new_request=False,
            title="Купить билеты на концерт Дорофеевой",
            event_date=event_date,
            event_time=time(10, 0),
            recurrence="none",
            confidence=0.99,
        ),
    ]

    with (
        patch.object(
            CalendarIntentInterpreter,
            "interpret",
            new=AsyncMock(side_effect=plans),
        ),
        patch.object(
            GoogleWorkspaceTools,
            "create_calendar_event",
            new=AsyncMock(
                return_value={
                    "id": "event-concert",
                    "summary": "Купить билеты на концерт Дорофеевой",
                }
            ),
        ) as create_event,
    ):
        async with factory.begin() as session:
            first_response = await MainOrchestrator.process_user_message(
                session=session,
                user_id=user_id,
                household_id=household_id,
                user_name="Денис",
                message_text="Завтра таск купить билеты на концерт",
                telegram_chat_id=chat_id,
                pending_actions_enabled=True,
            )

        async with factory.begin() as session:
            second_response = await MainOrchestrator.process_user_message(
                session=session,
                user_id=user_id,
                household_id=household_id,
                user_name="Денис",
                message_text="В 10:00, концерт Дорофеевой, и готово",
                telegram_chat_id=chat_id,
                pending_actions_enabled=True,
            )

    async with factory() as session:
        action = (await session.execute(select(PendingSharedAction))).scalar_one()

    assert "На какое время" in first_response
    assert "Купить билеты на концерт Дорофеевой" in second_response
    assert action.status == "completed"
    assert action.payload["title"] == "Купить билеты на концерт"
    assert create_event.await_args.kwargs["summary"] == "Купить билеты на концерт Дорофеевой"
    assert create_event.await_args.kwargs["start_at"] == datetime.combine(
        event_date,
        time(10, 0),
        tzinfo=ZoneInfo("Europe/Kyiv"),
    )
    await engine.dispose()


@pytest.mark.asyncio
async def test_semantic_daily_task_uses_any_title_and_inclusive_end_date() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    plan = SemanticCalendarPlan(
        intent="calendar",
        is_new_request=True,
        title="Читать книгу",
        event_date=date(2026, 8, 3),
        event_time=time(20, 0),
        recurrence="daily",
        recurrence_end_date=date(2026, 8, 6),
        confidence=0.99,
    )

    with (
        patch("app.orchestration.orchestrator.datetime", wraps=datetime) as clock,
        patch.object(
            CalendarIntentInterpreter,
            "interpret",
            new=AsyncMock(return_value=plan),
        ),
        patch.object(
            GoogleWorkspaceTools,
            "create_calendar_event",
            new=AsyncMock(return_value={"id": "event-book", "summary": "Читать книгу"}),
        ) as create_event,
    ):
        clock.now.return_value = datetime(2026, 8, 3, 8, tzinfo=timezone.utc)
        async with factory.begin() as session:
            await MainOrchestrator.process_user_message(
                session=session,
                user_id=user_id,
                household_id=household_id,
                user_name="Денис",
                message_text="С сегодняшнего дня до четверга каждый день в 20:00 читать книгу",
                telegram_chat_id=123456789,
                pending_actions_enabled=True,
            )

    assert create_event.await_args.kwargs["summary"] == "Читать книгу"
    assert create_event.await_args.kwargs["recurrence"] == ["RRULE:FREQ=DAILY;UNTIL=20260806T170000Z"]
    await engine.dispose()
