import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import CopyTextButton, InlineKeyboardMarkup

from app.domains.identity.service import ActorContext
from app.telegram.bot import handle_user_message


class _ExpenseMessage:
    def __init__(self) -> None:
        self.from_user = SimpleNamespace(id=123456789, first_name="Denys")
        self.chat = SimpleNamespace(id=123456789, type="private")
        self.message_id = 101
        self.text = "560 грн отдых"
        self.caption = None
        self.photo = None
        self.document = None
        self.audio = None
        self.voice = None
        self.answers: list[tuple[str, dict]] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append((text, kwargs))
        return SimpleNamespace(message_id=102)


@pytest.mark.asyncio
async def test_finance_confirmation_has_mobile_copy_button_with_exact_command() -> None:
    message = _ExpenseMessage()
    actor = ActorContext(
        user_id=uuid.uuid4(),
        telegram_id=message.from_user.id,
        household_id=uuid.uuid4(),
        chat_id=message.chat.id,
        chat_type="private",
    )
    response_text = "💳 Подтвердите запись расхода: 560 грн — отдых.\nНапишите: `подтвердить Z0xVU0TU`"

    @asynccontextmanager
    async def fake_uow():
        yield object()

    with (
        patch("app.telegram.bot.unit_of_work", new=fake_uow),
        patch("app.telegram.bot._resolve_actor", new=AsyncMock(return_value=actor)),
        patch(
            "app.telegram.bot.MainOrchestrator.process_user_message",
            new=AsyncMock(return_value=response_text),
        ),
    ):
        await handle_user_message(message)

    assert len(message.answers) == 1
    delivered_text, kwargs = message.answers[0]
    assert delivered_text == response_text
    reply_markup = kwargs.get("reply_markup")
    assert isinstance(reply_markup, InlineKeyboardMarkup)
    button = reply_markup.inline_keyboard[0][0]
    assert button.text == "Скопировать команду"
    assert isinstance(button.copy_text, CopyTextButton)
    assert button.copy_text.text == "подтвердить Z0xVU0TU"
    assert "Подтвердите запись расхода" not in button.copy_text.text
