import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import CallbackQuery, Chat, CopyTextButton, InlineKeyboardMarkup, Message, User

from app.domains.identity.service import ActorContext
from app.integrations.google.sheets import MONTHLY_BUDGET_HEADERS
from app.telegram.bot import handle_expense_category, handle_user_message


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


@pytest.mark.asyncio
async def test_expense_selection_displays_template_categories_without_internal_marker():
    message = _ExpenseMessage()
    actor = ActorContext(
        user_id=uuid.uuid4(),
        telegram_id=message.from_user.id,
        household_id=uuid.uuid4(),
        chat_id=message.chat.id,
        chat_type="private",
    )
    committed = False

    @asynccontextmanager
    async def fake_uow():
        nonlocal committed
        yield object()
        committed = True

    response = "💳 760 грн · 07.09.2026\nВыберите категорию кнопкой.\n`категория Z0xVU0TU 0`"
    with (
        patch("app.telegram.bot.unit_of_work", new=fake_uow),
        patch("app.telegram.bot._resolve_actor", new=AsyncMock(return_value=actor)),
        patch("app.telegram.bot.MainOrchestrator.process_user_message", AsyncMock(return_value=response)),
    ):
        await handle_user_message(message)
    assert committed
    text, kwargs = message.answers[0]
    assert "Z0xVU0TU" not in text
    buttons = [button for row in kwargs["reply_markup"].inline_keyboard for button in row]
    assert [b.text for b in buttons] == list(MONTHLY_BUDGET_HEADERS)
    assert [b.callback_data for b in buttons] == [f"expense:Z0xVU0TU:0:{i}" for i in range(10)]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_commit", [False, True])
async def test_category_callback_uses_clicking_user_and_only_reports_success_after_commit(fail_commit):
    message = Message(
        message_id=102,
        date=datetime.now(timezone.utc),
        chat=Chat(id=-100, type="supergroup"),
        from_user=User(id=999, is_bot=True, first_name="Bot"),
    )
    callback = CallbackQuery(
        id="selection",
        from_user=User(id=123, is_bot=False, first_name="One"),
        chat_instance="instance",
        message=message,
        data="expense:Z0xVU0TU:0:7",
    )
    actor = ActorContext(
        user_id=uuid.uuid4(),
        telegram_id=123,
        household_id=uuid.uuid4(),
        chat_id=-100,
        chat_type="supergroup",
    )
    committed = False

    @asynccontextmanager
    async def fake_uow():
        nonlocal committed
        yield object()
        if fail_commit:
            raise RuntimeError("commit failed")
        committed = True

    async def edit(*args, **kwargs):
        assert committed

    with (
        patch("app.telegram.bot.unit_of_work", new=fake_uow),
        patch("app.telegram.bot.IdentityService.resolve_actor", AsyncMock(return_value=actor)) as resolve,
        patch("app.telegram.bot.IdentityService.validate_domain_access"),
        patch("app.telegram.bot.select_expense_category", AsyncMock(return_value="💳 Расход сохранён: 760 грн · Быт")),
        patch.object(CallbackQuery, "answer", AsyncMock()),
        patch.object(Message, "answer", AsyncMock()) as answer,
        patch.object(Message, "edit_text", AsyncMock(side_effect=edit)) as edited,
    ):
        await handle_expense_category(callback)
    assert resolve.await_args.kwargs["telegram_user_id"] == 123
    assert resolve.await_args.kwargs["chat_id"] == -100
    if fail_commit:
        edited.assert_not_awaited()
        assert "Не удалось сохранить" in answer.await_args.args[0]
    else:
        edited.assert_awaited_once()
        answer.assert_not_awaited()
