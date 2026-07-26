import io
import logging
from typing import Any

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand

from app.config.settings import settings
from app.domains.identity.service import (
    ActorContext,
    IdentityService,
    PermissionDeniedError,
)
from app.infrastructure.database.session import unit_of_work
from app.integrations.oura.client import OuraClient
from app.orchestration.orchestrator import MainOrchestrator
from app.security.oauth import OAuthStateManager

logger = logging.getLogger(__name__)


def _secret_value(value: Any) -> str:
    """Accept plain strings and Pydantic SecretStr without leaking either."""
    get_secret_value = getattr(value, "get_secret_value", None)
    return get_secret_value() if callable(get_secret_value) else str(value)


bot = Bot(token=_secret_value(settings.TELEGRAM_BOT_TOKEN))
dp = Dispatcher()


def is_authorized_user(telegram_id: int) -> bool:
    """Cheap pre-filter; database identity resolution remains authoritative."""
    allowed = {settings.DENYS_TELEGRAM_ID, settings.OLEKSANDRA_TELEGRAM_ID}
    return telegram_id in allowed


def _message_coordinates(message: types.Message) -> tuple[int, int, str]:
    if message.from_user is None:
        raise PermissionDeniedError("Telegram update has no authenticated sender.")

    telegram_id = message.from_user.id
    chat = message.chat
    chat_id = chat.id
    chat_type = getattr(chat.type, "value", chat.type)
    return telegram_id, chat_id, str(chat_type)


async def _resolve_actor(session: Any, message: types.Message) -> ActorContext:
    telegram_id, chat_id, chat_type = _message_coordinates(message)
    return await IdentityService.resolve_actor(
        session,
        telegram_user_id=telegram_id,
        chat_id=chat_id,
        chat_type=chat_type,
    )


async def _answer_access_denied(message: types.Message) -> None:
    await message.answer(
        "🔒 Доступ запрещён. Используйте зарегистрированный личный чат или разрешённую семейную группу."
    )


async def setup_bot_commands(bot_instance: Bot) -> None:
    """Register the public Telegram command menu."""
    commands = [
        BotCommand(command="start", description="🌿 Запустить семейного ассистента"),
        BotCommand(command="help", description="📖 Справка и возможности"),
        BotCommand(command="oura", description="💍 Подключить Oura Ring"),
        BotCommand(command="tasks", description="📋 Текущие задачи"),
        BotCommand(command="shopping", description="🛒 Семейный список покупок"),
        BotCommand(command="budget", description="💳 Расходы за месяц"),
        BotCommand(command="health", description="🥗 Сводка здоровья и питания"),
    ]
    await bot_instance.set_my_commands(commands)
    logger.info("Telegram bot commands registered.")


@dp.message(CommandStart())
async def cmd_start(message: types.Message) -> None:
    try:
        async with unit_of_work() as session:
            await _resolve_actor(session, message)
    except PermissionDeniedError:
        await _answer_access_denied(message)
        return

    await message.answer(
        "🌿 **Добро пожаловать в Family AI Life OS!**\n\n"
        "Я помогаю с семейными задачами, бюджетом, питанием и Oura Ring.\n"
        "Для безопасного подключения Oura используйте **/oura**.",
        parse_mode=ParseMode.MARKDOWN,
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    try:
        async with unit_of_work() as session:
            await _resolve_actor(session, message)
    except PermissionDeniedError:
        await _answer_access_denied(message)
        return

    await message.answer(
        "📖 **Доступные возможности:**\n"
        "• **/oura** — подключение Oura Ring только в личном чате\n"
        "• **/shopping** — семейный список покупок\n"
        "• **/tasks** — текущие задачи\n"
        "• **/budget** — расходы текущего месяца\n"
        "• **/health** — личная сводка здоровья\n"
        "• отправьте фото еды для приблизительной оценки состава",
        parse_mode=ParseMode.MARKDOWN,
    )


@dp.message(Command("oura"))
async def cmd_oura_setup(message: types.Message) -> None:
    try:
        async with unit_of_work() as session:
            actor = await _resolve_actor(session, message)
            IdentityService.validate_domain_access(actor, "oauth")
            raw_state, _ = await OAuthStateManager.create_state(
                session,
                user_id=actor.user_id,
                provider="oura",
            )
            auth_url = OuraClient.get_authorization_url(state=raw_state)
    except PermissionDeniedError:
        await _answer_access_denied(message)
        return
    except Exception:
        logger.error("Failed to initialize Oura authorization.")
        await message.answer("❌ Не удалось начать авторизацию Oura. Попробуйте ещё раз позже.")
        return

    # The transaction containing the one-time state has committed at this point.
    await message.answer(
        "💍 **Подключение Oura Ring:**\n\n"
        f"[Открыть защищённую страницу авторизации Oura]({auth_url})\n\n"
        "После подтверждения вернитесь в Telegram. "
        "Копировать callback URL или код авторизации в чат не нужно.",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


@dp.message()
async def handle_user_message(message: types.Message) -> None:
    if message.from_user is None:
        await _answer_access_denied(message)
        return

    user_name = message.from_user.first_name or "Пользователь"
    text = message.text or message.caption or ""
    response_text: str

    try:
        async with unit_of_work() as session:
            actor = await _resolve_actor(session, message)
            domain = MainOrchestrator.domain_for_message(
                text,
                has_photo=bool(message.photo),
                has_document=bool(message.document),
            )
            IdentityService.validate_domain_access(actor, domain)

            photo_bytes = None
            if message.photo:
                photo = message.photo[-1]
                file_info = await bot.get_file(photo.file_id)
                if not file_info.file_path:
                    raise ValueError("Telegram returned an empty photo path.")
                photo_buffer = io.BytesIO()
                await bot.download_file(file_info.file_path, destination=photo_buffer)
                photo_bytes = photo_buffer.getvalue()

            response_text = await MainOrchestrator.process_user_message(
                session=session,
                user_id=actor.user_id,
                household_id=actor.household_id,
                user_name=user_name,
                message_text=text,
                photo_bytes=photo_bytes,
            )
    except PermissionDeniedError:
        await _answer_access_denied(message)
        return
    except Exception:
        # Never echo provider payloads, authorization codes, or traceback text.
        logger.error("Telegram update failed before commit.")
        await message.answer("❌ Не удалось выполнить запрос. Изменения не были сохранены.")
        return

    # A success response is sent only after the Unit of Work has committed.
    await message.answer(response_text, parse_mode=ParseMode.MARKDOWN)


async def start_bot() -> None:
    logger.info("Starting Telegram polling.")
    await setup_bot_commands(bot)
    await dp.start_polling(bot)
