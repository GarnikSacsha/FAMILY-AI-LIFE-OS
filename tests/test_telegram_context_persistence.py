import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

import pytest

from app.config.settings import settings
from app.domains.identity.service import ActorContext
from app.telegram.bot import handle_user_message


class _GroupMessage:
    def __init__(self) -> None:
        self.from_user = SimpleNamespace(id=123456789, first_name="Denys")
        self.chat = SimpleNamespace(id=-100123, type="group")
        self.message_id = 41
        self.text = "Что у нас на завтра?"
        self.caption = None
        self.photo = None
        self.document = None
        self.answers = []

    async def answer(self, text: str, **kwargs):
        self.answers.append((text, kwargs))
        return SimpleNamespace(message_id=42)


@pytest.mark.asyncio
async def test_delivered_shared_response_is_queued_when_context_persistence_fails(monkeypatch) -> None:
    """A delivered group reply must remain recoverable after a database failure."""
    message = _GroupMessage()
    actor = ActorContext(
        user_id=uuid.uuid4(),
        telegram_id=message.from_user.id,
        household_id=uuid.uuid4(),
        chat_id=message.chat.id,
        chat_type="group",
    )
    monkeypatch.setattr(settings, "SHARED_CHAT_MEMORY_ENABLED", True)

    @asynccontextmanager
    async def fake_uow():
        yield object()

    record_message = AsyncMock(side_effect=[None, RuntimeError("database unavailable")])
    queue_for_retry = AsyncMock()

    with (
        patch("app.telegram.bot.unit_of_work", new=fake_uow),
        patch("app.telegram.bot._resolve_actor", new=AsyncMock(return_value=actor)),
        patch(
            "app.telegram.bot.MainOrchestrator.process_user_message",
            new=AsyncMock(return_value="Завтра без встреч."),
        ),
        patch("app.telegram.bot.SharedMemoryTools.record_message", new=record_message),
        patch("app.telegram.bot.SharedMemoryTools.queue_message_for_retry", new=queue_for_retry),
    ):
        await handle_user_message(message)

    assert message.answers == [("Завтра без встреч.", {"parse_mode": "Markdown"})]
    queue_for_retry.assert_awaited_once_with(
        ANY,
        household_id=actor.household_id,
        telegram_chat_id=actor.chat_id,
        telegram_message_id=42,
        author_name="Family",
        message_type="text",
        content="Завтра без встреч.",
    )
