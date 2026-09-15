import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.types import User as TelegramUser
from sqlalchemy import select

from app.domains.identity.service import ActorContext, PermissionDeniedError
from app.domains.planning.models import ShoppingItem, Task
from app.telegram.planner import edit_task_reply, planner_callback, planner_page
from app.tools.planner_tools import PlannerTools
from tests.test_shared_memory import _memory_database, _seed_family


@pytest.mark.asyncio
async def test_task_changes_are_scoped_idempotent_and_survive_new_session():
    engine, factory = await _memory_database()
    household, user = await _seed_family(factory)
    async with factory.begin() as session:
        result = await PlannerTools.create_task(session, user, "household", household, "Original")
        task_id = uuid.UUID(result["task_id"])
    async with factory.begin() as session:
        assert not await PlannerTools.update_task(session, household_id=uuid.uuid4(), task_id=task_id, complete=True)
        assert await PlannerTools.update_task(session, household_id=household, task_id=task_id, title=" New  title ")
        assert await PlannerTools.update_task(
            session,
            household_id=household,
            task_id=task_id,
            due_date=datetime(2026, 10, 1, tzinfo=timezone.utc),
            change_due_date=True,
        )
    async with factory.begin() as session:
        task = await session.get(Task, task_id)
        assert task.title == "New title" and task.due_date is not None
        assert await PlannerTools.update_task(session, household_id=household, task_id=task_id, change_due_date=True)
        assert task.due_date is None
        assert await PlannerTools.update_task(session, household_id=household, task_id=task_id, complete=True)
        assert not await PlannerTools.update_task(session, household_id=household, task_id=task_id, complete=True)
        assert not await PlannerTools.update_task(session, household_id=household, task_id=task_id, title="late")
        assert await PlannerTools.get_active_tasks(session, owner_id=household) == []
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("title", ["", "  ", "x" * 256])
async def test_invalid_title_does_not_modify_task(title):
    engine, factory = await _memory_database()
    household, user = await _seed_family(factory)
    async with factory.begin() as session:
        result = await PlannerTools.create_task(session, user, "household", household, "Original")
        with pytest.raises(ValueError):
            await PlannerTools.update_task(
                session, household_id=household, task_id=uuid.UUID(result["task_id"]), title=title
            )
        assert (await PlannerTools.get_active_tasks(session, owner_id=household))[0]["title"] == "Original"
    await engine.dispose()


@pytest.mark.asyncio
async def test_shopping_completion_and_pagination_use_stable_ids():
    engine, factory = await _memory_database()
    async with engine.begin() as conn:
        await conn.run_sync(ShoppingItem.__table__.create)
    household, user = await _seed_family(factory)
    actor = ActorContext(user_id=user, household_id=household, telegram_id=123, chat_id=123, chat_type="private")
    async with factory.begin() as session:
        for i in range(10):
            await PlannerTools.add_shopping_item(session, household, user, f"Item {i}")
        text, markup = await planner_page(session, actor, "shopping")
        assert "Далее" in markup.inline_keyboard[-1][0].text
        reference = uuid.UUID(markup.inline_keyboard[0][0].callback_data.split(":")[-1])
        assert not await PlannerTools.complete_shopping_item(session, household_id=uuid.uuid4(), item_id=reference)
        assert await PlannerTools.complete_shopping_item(session, household_id=household, item_id=reference)
        assert not await PlannerTools.complete_shopping_item(session, household_id=household, item_id=reference)
        assert len(await PlannerTools.get_active_shopping_list(session, household)) == 9
        assert len(list((await session.execute(select(ShoppingItem))).scalars())) == 10
    await engine.dispose()


def _message(text, reply=None):
    return Message(
        message_id=2,
        date=datetime.now(timezone.utc),
        chat=Chat(id=123, type="private"),
        from_user=TelegramUser(id=123, is_bot=False, first_name="User"),
        text=text,
        reply_to_message=reply,
    )


@pytest.mark.asyncio
async def test_callback_resolves_clicker_and_commits_before_reply():
    engine, factory = await _memory_database()
    household, user = await _seed_family(factory)
    async with factory.begin() as session:
        result = await PlannerTools.create_task(session, user, "household", household, "Original")
    actor = ActorContext(user_id=user, household_id=household, telegram_id=123, chat_id=123, chat_type="private")
    callback = CallbackQuery(
        id="1",
        from_user=_message("").from_user,
        chat_instance="1",
        message=_message("list"),
        data=f"plan:done:{uuid.UUID(result['task_id']).hex}",
    )

    async def answer(*args, **kwargs):
        async with factory() as session:
            assert (await session.get(Task, uuid.UUID(result["task_id"]))).is_completed

    with (
        patch("app.telegram.planner.unit_of_work", factory.begin),
        patch("app.telegram.planner.IdentityService.resolve_actor", AsyncMock(return_value=actor)) as resolve,
        patch.object(CallbackQuery, "answer", AsyncMock()),
        patch.object(Message, "answer", new=answer),
    ):
        await planner_callback(callback)
    assert resolve.call_args.kwargs["telegram_user_id"] == 123
    await engine.dispose()


@pytest.mark.asyncio
async def test_unauthorized_callback_never_mutates():
    engine, factory = await _memory_database()
    callback = CallbackQuery(
        id="1",
        from_user=_message("").from_user,
        chat_instance="1",
        message=_message("list"),
        data=f"plan:buy:{uuid.uuid4().hex}",
    )
    with (
        patch("app.telegram.planner.unit_of_work", factory.begin),
        patch("app.telegram.planner.IdentityService.resolve_actor", AsyncMock(side_effect=PermissionDeniedError)),
        patch.object(CallbackQuery, "answer", AsyncMock()),
        patch.object(Message, "answer", AsyncMock()),
        patch.object(PlannerTools, "complete_shopping_item", AsyncMock()) as mutate,
    ):
        await planner_callback(callback)
        mutate.assert_not_awaited()
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("title", "Renamed", "Renamed"),
        ("date", "01.10.2026 15:30", "date"),
        ("date", "30.02.2026 15:30", "unchanged"),
        ("date", "25.10.2026 03:30", "unchanged"),
        ("title", "отмена", "unchanged"),
    ],
)
async def test_edit_reply_updates_only_referenced_task(field, value, expected):
    engine, factory = await _memory_database()
    household, user = await _seed_family(factory)
    async with factory.begin() as session:
        result = await PlannerTools.create_task(session, user, "household", household, "Original")
    actor = ActorContext(user_id=user, household_id=household, telegram_id=123, chat_id=123, chat_type="private")
    bot = Bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
    prompt = _message("Новое название").model_copy(
        update={
            "from_user": TelegramUser(id=bot.id, is_bot=True, first_name="Bot"),
            "reply_markup": InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Отмена", callback_data=f"edit:{field}:{uuid.UUID(result['task_id']).hex}:123"
                        )
                    ]
                ]
            ),
        }
    )
    reply = _message(value, prompt).as_(bot)
    with (
        patch("app.telegram.planner.unit_of_work", factory.begin),
        patch("app.telegram.planner.IdentityService.resolve_actor", AsyncMock(return_value=actor)),
        patch.object(Message, "answer", AsyncMock()),
    ):
        await edit_task_reply(reply)
    async with factory() as session:
        task = await session.get(Task, uuid.UUID(result["task_id"]))
        if expected == "date":
            assert task.due_date == datetime(2026, 10, 1, 12, 30)
        else:
            assert task.title == ("Original" if expected == "unchanged" else expected)
            assert task.due_date is None
    await bot.session.close()
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["title", "date"])
async def test_edit_button_produces_reply_prompt_without_mutation(action):
    engine, factory = await _memory_database()
    household, user = await _seed_family(factory)
    async with factory.begin() as session:
        result = await PlannerTools.create_task(session, user, "household", household, "Original")
    actor = ActorContext(user_id=user, household_id=household, telegram_id=123, chat_id=123, chat_type="private")
    callback = CallbackQuery(
        id="1",
        from_user=_message("").from_user,
        chat_instance="1",
        message=_message("list"),
        data=f"plan:{action}:{uuid.UUID(result['task_id']).hex}",
    )
    with (
        patch("app.telegram.planner.unit_of_work", factory.begin),
        patch("app.telegram.planner.IdentityService.resolve_actor", AsyncMock(return_value=actor)),
        patch.object(CallbackQuery, "answer", AsyncMock()),
        patch.object(Message, "answer", AsyncMock()) as answer,
    ):
        await planner_callback(callback)
    assert "Ответ" in answer.call_args.args[0]
    assert result["task_id"] not in answer.call_args.args[0]
    data = answer.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
    assert data.startswith(f"edit:{action}:") and len(data.encode()) <= 64
    async with factory() as session:
        assert (await session.get(Task, uuid.UUID(result["task_id"]))).title == "Original"
    await engine.dispose()
