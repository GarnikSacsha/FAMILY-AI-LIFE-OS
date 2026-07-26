import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

import pytest

from app.domains.identity.service import ActorContext
from app.telegram.bot import cmd_help, cmd_oura_setup, cmd_start, handle_user_message


class Message:
    def __init__(self, text: str, *, chat_id: int, chat_type: str):
        self.from_user = SimpleNamespace(id=123456789, first_name="Denys")
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.text = text
        self.caption = None
        self.photo = None
        self.document = None
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


@pytest.mark.asyncio
async def test_group_cannot_access_health_domain():
    message = Message(
        "Покажи здоровье",
        chat_id=-100123,
        chat_type="group",
    )
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
        patch(
            "app.telegram.bot.IdentityService.resolve_actor",
            new=AsyncMock(return_value=actor),
        ),
        patch(
            "app.telegram.bot.MainOrchestrator.process_user_message",
            new=AsyncMock(),
        ) as process,
    ):
        await handle_user_message(message)

    process.assert_not_awaited()
    assert "Доступ запрещён" in message.answers[-1][0]


@pytest.mark.asyncio
async def test_oura_command_creates_random_state_for_internal_user():
    message = Message("/oura", chat_id=123456789, chat_type="private")
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
            "app.telegram.bot.OAuthStateManager.create_state",
            new=AsyncMock(return_value=("random-state", object())),
        ) as create_state,
        patch(
            "app.telegram.bot.OuraClient.get_authorization_url",
            return_value="https://oura.example/authorize?state=random-state",
        ),
    ):
        await cmd_oura_setup(message)

    create_state.assert_awaited_once_with(
        ANY,
        user_id=actor.user_id,
        provider="oura",
    )
    assert "Копировать callback URL" in message.answers[-1][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", [cmd_start, cmd_help])
async def test_commands_answer_when_database_is_unavailable(handler):
    message = Message(
        f"/{handler.__name__}",
        chat_id=123456789,
        chat_type="private",
    )

    @asynccontextmanager
    async def failing_uow():
        raise RuntimeError("simulated database failure")
        yield

    with patch("app.telegram.bot.unit_of_work", new=failing_uow):
        await handler(message)

    assert message.answers
    assert "временно недоступен" in message.answers[-1][0]
