from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.domains.planning.models import Task
from app.orchestration.orchestrator import MainOrchestrator
from tests.test_shared_memory import _memory_database, _seed_family


def test_explicit_task_marker_has_priority_over_calendar_domain() -> None:
    message = "Добавь в таски для меня на завтра.\n\nКупить порошок."

    assert MainOrchestrator.domain_for_message(message) == "planner"


def test_explicit_task_command_stays_in_planner_domain() -> None:
    assert MainOrchestrator.domain_for_message("Создай задачу купить корм") == "planner"
    assert MainOrchestrator.domain_for_message("Добавь встречу на завтра") == "calendar"


@pytest.mark.asyncio
async def test_multiline_task_list_creates_due_tasks_for_current_user() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    message = (
        "Добавь в таски для меня на завтра.\n\n"
        "Купить порошок.\n"
        "Разобрать вещи.\n"
        "Постирать вещи.\n"
        "Набрать воды.\n"
        "Купить продукты.\n"
        "Заняться огурцами.\n"
        "Положить 5 тысяч на карту.\n"
        "Подключить пылесос к интернету."
    )

    async with factory.begin() as session:
        response = await MainOrchestrator.process_user_message(
            session=session,
            user_id=user_id,
            household_id=household_id,
            user_name="Саша",
            message_text=message,
            timezone_name="Europe/Kyiv",
            telegram_chat_id=-100123,
            shared_context_enabled=True,
        )

    async with factory() as session:
        tasks = list((await session.execute(select(Task).order_by(Task.created_at, Task.id))).scalars().all())

    assert [task.title for task in tasks] == [
        "Купить порошок",
        "Разобрать вещи",
        "Постирать вещи",
        "Набрать воды",
        "Купить продукты",
        "Заняться огурцами",
        "Положить 5 тысяч на карту",
        "Подключить пылесос к интернету",
    ]
    assert all(task.owner_type == "household" and task.owner_id == household_id for task in tasks)
    assert all(task.creator_id == user_id and task.assignee_id == user_id for task in tasks)
    tomorrow = datetime.now(ZoneInfo("Europe/Kyiv")).date() + timedelta(days=1)
    assert all(
        task.due_date.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Kyiv")).date() == tomorrow
        for task in tasks
    )
    assert "Создал семейные задачи" in response
    await engine.dispose()
