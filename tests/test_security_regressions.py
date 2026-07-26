import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.identity.service import ActorContext
from app.telegram.bot import handle_user_message


class RecordingSession:
    def __init__(self, events):
        self.events = events
        self.staged = []
        self.persisted = []

    def add(self, entity):
        self.staged.append(entity)

    async def flush(self):
        self.events.append("flush")


class Message:
    def __init__(self, text: str, events=None):
        self.from_user = SimpleNamespace(id=123456789, first_name="Denys")
        self.chat = SimpleNamespace(id=123456789, type="private")
        self.text = text
        self.caption = None
        self.photo = None
        self.document = None
        self.events = events if events is not None else []
        self.answers = []

    async def answer(self, text, **kwargs):
        self.events.append("answer")
        self.answers.append((text, kwargs))


@pytest.mark.asyncio
async def test_successful_telegram_write_commits_before_success_reply():
    events = []
    session = RecordingSession(events)
    message = Message("Добавь молоко в список покупок, купить", events)
    actor = ActorContext(
        user_id=uuid.uuid4(),
        telegram_id=message.from_user.id,
        household_id=uuid.uuid4(),
        chat_id=message.chat.id,
        chat_type="private",
    )

    @asynccontextmanager
    async def fake_uow():
        try:
            yield session
        except Exception:
            session.staged.clear()
            events.append("rollback")
            raise
        else:
            session.persisted.extend(session.staged)
            session.staged.clear()
            events.append("commit")

    with (
        patch("app.telegram.bot.unit_of_work", new=fake_uow),
        patch(
            "app.telegram.bot.IdentityService.resolve_actor",
            new=AsyncMock(return_value=actor),
        ),
    ):
        await handle_user_message(message)

    assert len(session.persisted) == 1
    assert "молоко" in message.answers[-1][0].lower()
    assert events.index("commit") < events.index("answer")


@pytest.mark.asyncio
async def test_long_plain_text_never_enters_oauth_exchange():
    message = Message("Это обычное семейное сообщение, которое намного длиннее двадцати символов.")
    actor = ActorContext(
        user_id=uuid.uuid4(),
        telegram_id=message.from_user.id,
        household_id=uuid.uuid4(),
        chat_id=message.chat.id,
        chat_type="private",
    )

    @asynccontextmanager
    async def fake_uow():
        yield object()

    with (
        patch("app.telegram.bot.unit_of_work", new=fake_uow),
        patch(
            "app.telegram.bot.IdentityService.resolve_actor",
            new=AsyncMock(return_value=actor),
        ),
        patch(
            "app.telegram.bot.MainOrchestrator.process_user_message",
            new=AsyncMock(return_value="Обычный ответ"),
        ),
        patch(
            "app.telegram.bot.OuraClient.exchange_code_for_tokens",
            new=AsyncMock(),
        ) as exchange,
    ):
        await handle_user_message(message)

    exchange.assert_not_awaited()
    assert message.answers[-1][0] == "Обычный ответ"
