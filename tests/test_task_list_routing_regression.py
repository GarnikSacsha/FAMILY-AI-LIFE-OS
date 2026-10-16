import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, select

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
    task_ids = iter(range(8, 0, -1))

    def force_timestamp_ties(mapper, connection, target):
        # Real clocks can return the same timestamp for successive inserts.
        target.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        target.id = uuid.UUID(int=next(task_ids))

    event.listen(Task, "before_insert", force_timestamp_ties)
    try:
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

        expected_titles = [
            "Купить порошок",
            "Разобрать вещи",
            "Постирать вещи",
            "Набрать воды",
            "Купить продукты",
            "Заняться огурцами",
            "Положить 5 тысяч на карту",
            "Подключить пылесос к интернету",
        ]
        # Storage ordering is timestamp/UUID ordering, not input ordering.
        # Counter preserves multiplicity: missing, duplicate and extra rows fail.
        assert Counter(task.title for task in tasks) == Counter(expected_titles)
        assert len({task.created_at for task in tasks}) == 1
        assert [task.title for task in tasks] == list(reversed(expected_titles))
        # The user-visible response must still preserve the exact input order.
        assert response == "📋 Создал семейные задачи:\n" + "\n".join(
            f"• {title}" for title in expected_titles
        )
        assert all(task.owner_type == "household" and task.owner_id == household_id for task in tasks)
        assert all(task.creator_id == user_id and task.assignee_id == user_id for task in tasks)
        tomorrow = datetime.now(ZoneInfo("Europe/Kyiv")).date() + timedelta(days=1)
        assert all(
            task.due_date.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Kyiv")).date() == tomorrow
            for task in tasks
        )
        assert "Создал семейные задачи" in response
    finally:
        event.remove(Task, "before_insert", force_timestamp_ties)
        await engine.dispose()
