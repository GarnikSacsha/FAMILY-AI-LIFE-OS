"""Household planner controls with stable row IDs and per-action authorization."""

import logging
import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.service import ActorContext, IdentityService, PermissionDeniedError
from app.infrastructure.database.session import unit_of_work
from app.tools.planner_tools import PlannerTools

logger = logging.getLogger(__name__)
PAGE_SIZE = 8
EDIT_MARKER = re.compile(r"edit:(title|date):([0-9a-f]{32}):(\d+)$")


def edit_reference(message: types.Message) -> re.Match[str] | None:
    markup = message.reply_markup
    if markup and markup.inline_keyboard:
        return EDIT_MARKER.fullmatch(markup.inline_keyboard[0][0].callback_data or "")
    return None


def is_edit_reply(message: types.Message) -> bool:
    return message.reply_to_message is not None and edit_reference(message.reply_to_message) is not None


async def _actor(session: AsyncSession, message: types.Message, sender_id: int) -> ActorContext:
    actor = await IdentityService.resolve_actor(
        session,
        telegram_user_id=sender_id,
        chat_id=message.chat.id,
        chat_type=str(getattr(message.chat.type, "value", message.chat.type)),
    )
    IdentityService.validate_domain_access(actor, "planner")
    return actor


async def planner_page(
    session: AsyncSession,
    actor: ActorContext,
    kind: str,
    offset: int = 0,
) -> tuple[str, InlineKeyboardMarkup]:
    if kind == "tasks":
        rows = await PlannerTools.get_active_tasks(
            session,
            owner_id=actor.household_id,
            limit=PAGE_SIZE + 1,
            offset=offset,
        )
    else:
        rows = await PlannerTools.get_active_shopping_list(
            session,
            actor.household_id,
            limit=PAGE_SIZE + 1,
            offset=offset,
        )
    if not rows and offset:
        return await planner_page(session, actor, kind)
    lines = ["📋 Активные семейные задачи:" if kind == "tasks" else "🛒 Семейный список покупок:"]
    buttons: list[list[InlineKeyboardButton]] = []
    for index, row in enumerate(rows[:PAGE_SIZE], offset + 1):
        row_id = uuid.UUID(row["task_id"] if kind == "tasks" else row["id"]).hex
        title = row["title"] if kind == "tasks" else row["item_name"]
        suffix = ""
        if kind == "tasks" and row.get("due_date"):
            due = datetime.fromisoformat(row["due_date"])
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            suffix = f" — до {due.astimezone(ZoneInfo(actor.timezone)):%d.%m.%Y %H:%M}"
        elif kind == "shopping" and row.get("quantity"):
            suffix = f" — {row['quantity']}"
        lines.append(f"{index}. {title}{suffix}")
        if kind == "tasks":
            buttons.append(
                [
                    InlineKeyboardButton(text=f"✓ {index}", callback_data=f"plan:done:{row_id}"),
                    InlineKeyboardButton(text=f"✏️ {index}", callback_data=f"plan:title:{row_id}"),
                    InlineKeyboardButton(text=f"📅 {index}", callback_data=f"plan:date:{row_id}"),
                ]
            )
        else:
            buttons.append([InlineKeyboardButton(text=f"✓ Куплено: {index}", callback_data=f"plan:buy:{row_id}")])
    if not rows:
        lines.append("Пока пусто.")
    elif kind == "tasks":
        lines.append("\n✓ — выполнено · ✏️ — название · 📅 — срок")
    navigation = []
    if offset:
        navigation.append(
            InlineKeyboardButton(text="← Назад", callback_data=f"plan:{kind}:{max(0, offset - PAGE_SIZE)}")
        )
    if len(rows) > PAGE_SIZE:
        navigation.append(InlineKeyboardButton(text="Далее →", callback_data=f"plan:{kind}:{offset + PAGE_SIZE}"))
    if navigation:
        buttons.append(navigation)
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


async def show_planner(message: types.Message) -> None:
    if message.from_user is None:
        return
    kind = "shopping" if (message.text or "").lower().startswith("/shopping") else "tasks"
    try:
        async with unit_of_work() as session:
            actor = await _actor(session, message, message.from_user.id)
            text, markup = await planner_page(session, actor, kind)
    except PermissionDeniedError:
        await message.answer("🔒 Доступ запрещён.")
        return
    except Exception:
        logger.error("Planner listing failed.")
        await message.answer("Не удалось загрузить список. Попробуйте ещё раз.")
        return
    await message.answer(text, reply_markup=markup, parse_mode=None)


async def planner_callback(callback: types.CallbackQuery) -> None:
    if not isinstance(callback.message, types.Message):
        await callback.answer("Сообщение недоступно.")
        return
    match = re.fullmatch(r"plan:(done|buy|title|date|tasks|shopping):([0-9a-f]{32}|\d{1,6})", callback.data or "")
    if match is None:
        await callback.answer("Некорректная кнопка.")
        return
    await callback.answer()
    action, reference = match.groups()
    try:
        async with unit_of_work() as session:
            actor = await _actor(session, callback.message, callback.from_user.id)
            if action in {"tasks", "shopping"}:
                if not reference.isdecimal() or len(reference) > 6:
                    return
                text, markup = await planner_page(session, actor, action, int(reference))
            elif action in {"title", "date"}:
                # Check existence and ownership before opening the editor; recheck on save.
                found = await PlannerTools.update_task(
                    session, household_id=actor.household_id, task_id=uuid.UUID(reference)
                )
                if not found:
                    text, markup = "Задача уже закрыта или недоступна. Обновите /tasks.", None
                else:
                    instruction = (
                        "Ответьте на это сообщение новым названием задачи (до 255 символов)."
                        if action == "title"
                        else (
                            f"Ответьте новым сроком: ДД.ММ.ГГГГ ЧЧ:ММ ({actor.timezone}). "
                            "Чтобы убрать срок, отправьте «-»."
                        )
                    )
                    text = f"{instruction}\nИспользуйте «Ответить» на этом сообщении. Для отмены нажмите кнопку."
                    markup = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="Отмена", callback_data=f"edit:{action}:{reference}:{actor.telegram_id}"
                                )
                            ]
                        ]
                    )
            else:
                if action == "done":
                    changed = await PlannerTools.update_task(
                        session, household_id=actor.household_id, task_id=uuid.UUID(reference), complete=True
                    )
                else:
                    changed = await PlannerTools.complete_shopping_item(
                        session, household_id=actor.household_id, item_id=uuid.UUID(reference)
                    )
                text, markup = await planner_page(session, actor, "tasks" if action == "done" else "shopping")
                text = ("✅ Сохранено.\n\n" if changed else "Уже отмечено или недоступно.\n\n") + text
    except PermissionDeniedError:
        await callback.message.answer("🔒 Доступ запрещён.")
        return
    except Exception:
        logger.error("Planner action failed before commit.")
        await callback.message.answer("Не удалось сохранить изменение. Попробуйте ещё раз.")
        return
    # Send only after commit. Old buttons remain safe because they use immutable IDs.
    await callback.message.answer(text, reply_markup=markup, parse_mode=None)


async def edit_task_reply(message: types.Message) -> None:
    reply = message.reply_to_message
    if reply is None or reply.from_user is None or message.from_user is None or message.bot is None:
        return
    if reply.from_user.id != message.bot.id:
        return
    match = edit_reference(reply)
    if match is None:
        return
    field, task_id, telegram_id = match.groups()
    if message.from_user.id != int(telegram_id):
        await message.answer("Ответить на это изменение может тот, кто нажал кнопку. Откройте /tasks.")
        return
    value = (message.text or "").strip()
    if value.lower() in {"отмена", "/cancel"}:
        await message.answer("Изменение отменено.")
        return
    try:
        async with unit_of_work() as session:
            actor = await _actor(session, message, message.from_user.id)
            due = None
            if field == "date" and value != "-":
                try:
                    due = datetime.strptime(value, "%d.%m.%Y %H:%M").replace(tzinfo=ZoneInfo(actor.timezone))
                except ValueError as exc:
                    raise ValueError(
                        "Нужен срок в формате ДД.ММ.ГГГГ ЧЧ:ММ или «-». Ответьте на то же сообщение ещё раз."
                    ) from exc
                # Reject nonexistent or ambiguous local times around a clock change.
                if (
                    due.astimezone(timezone.utc).astimezone(due.tzinfo) != due
                    or due.utcoffset() != due.replace(fold=1).utcoffset()
                ):
                    raise ValueError("Это время попадает на перевод часов. Укажите другое время.")
            changed = await PlannerTools.update_task(
                session,
                household_id=actor.household_id,
                task_id=uuid.UUID(task_id),
                title=value if field == "title" else None,
                due_date=due,
                change_due_date=field == "date",
            )
    except PermissionDeniedError:
        await message.answer("🔒 Доступ запрещён.")
        return
    except ValueError as error:
        await message.answer(str(error))
        return
    except Exception:
        logger.error("Task edit failed before commit.")
        await message.answer("Не удалось сохранить изменение. Попробуйте ещё раз.")
        return
    await message.answer("✅ Задача обновлена. /tasks" if changed else "Задача уже закрыта или недоступна. /tasks")


def register_planner_handlers(dispatcher: Dispatcher) -> None:
    dispatcher.message.register(show_planner, Command("tasks", "shopping"))
    dispatcher.callback_query.register(planner_callback, F.data.startswith("plan:"))
    dispatcher.callback_query.register(cancel_edit, F.data.startswith("edit:"))
    dispatcher.message.register(edit_task_reply, is_edit_reply)


async def cancel_edit(callback: types.CallbackQuery) -> None:
    if not isinstance(callback.message, types.Message):
        return
    match = EDIT_MARKER.fullmatch(callback.data or "")
    if match is None or callback.from_user.id != int(match.group(3)):
        await callback.answer("Отменить может тот, кто начал изменение.")
        return
    await callback.answer()
    await callback.message.edit_text("Изменение отменено. /tasks", reply_markup=None)
