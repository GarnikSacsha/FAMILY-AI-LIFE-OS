import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.orchestration.orchestrator import MainOrchestrator


@pytest.mark.asyncio
async def test_family_message_creates_tomorrow_reminder_for_named_member():
    denys_id = uuid.uuid4()
    oleksandra_id = uuid.uuid4()
    household_id = uuid.uuid4()
    users = [
        SimpleNamespace(id=denys_id, first_name="Denys"),
        SimpleNamespace(id=oleksandra_id, first_name="Oleksandra"),
    ]
    result = Mock()
    result.scalars.return_value.all.return_value = users
    session = Mock()
    session.execute = AsyncMock(return_value=result)

    with (
        patch(
            "app.orchestration.orchestrator.datetime",
            wraps=datetime,
        ) as clock,
        patch(
            "app.orchestration.orchestrator.PlannerTools.create_reminder",
            new=AsyncMock(
                return_value={
                    "title": "сделать страховку",
                    "status": "CREATED",
                }
            ),
        ) as create_reminder,
    ):
        clock.now.return_value = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
        response = await MainOrchestrator.process_user_message(
            session=session,
            user_id=oleksandra_id,
            household_id=household_id,
            user_name="Oleksandra",
            message_text="Деня, нужно завтра сделать страховку",
        )

    assert "Напомню" in response
    assert create_reminder.await_args.kwargs["recipient_id"] == denys_id
    assert create_reminder.await_args.kwargs["trigger_at"] == datetime(
        2026,
        7,
        28,
        6,
        tzinfo=timezone.utc,
    )


def test_general_response_domain_knows_calendar_is_private():
    assert MainOrchestrator.domain_for_message("Добавь в календарь завтра в 15:00 встречу") == "calendar"
