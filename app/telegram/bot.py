import io
import logging
import time
from typing import Any, cast

from aiogram import Bot, Dispatcher, types
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError
from aiogram.filters import Command, CommandStart
from aiogram.methods import GetUpdates, Response, TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import BotCommand

from app.config.settings import settings
from app.domains.identity.service import (
    ActorContext,
    IdentityService,
    PermissionDeniedError,
)
from app.infrastructure.database.session import unit_of_work
from app.integrations.google.oauth import GoogleOAuthClient
from app.integrations.oura.client import OuraClient
from app.orchestration.orchestrator import MainOrchestrator
from app.security.oauth import OAuthStateManager
from app.tools.google_tools import GoogleWorkspaceError, GoogleWorkspaceTools

logger = logging.getLogger(__name__)

POLLING_STALE_AFTER_SECONDS = 45.0


class TelegramPollingHealth:
    """Track real Telegram getUpdates responses instead of task existence."""

    def __init__(self) -> None:
        self._status = "disabled"
        self._last_success_at: float | None = None

    def reset(self, *, enabled: bool) -> None:
        self._status = "starting" if enabled else "disabled"
        self._last_success_at = None

    def mark_starting(self) -> None:
        self.reset(enabled=True)

    def mark_success(self) -> None:
        self._status = "running"
        self._last_success_at = time.monotonic()

    def mark_failure(self, *, conflict: bool = False) -> None:
        self._status = "conflict" if conflict else "unavailable"

    def mark_stopped(self) -> None:
        self._status = "stopped"

    def current_status(self) -> str:
        if (
            self._status == "running"
            and self._last_success_at is not None
            and time.monotonic() - self._last_success_at > POLLING_STALE_AFTER_SECONDS
        ):
            return "stale"
        return self._status


polling_health = TelegramPollingHealth()


def _secret_value(value: Any) -> str:
    """Accept plain strings and Pydantic SecretStr without leaking either."""
    get_secret_value = getattr(value, "get_secret_value", None)
    return get_secret_value() if callable(get_secret_value) else str(value)


bot = Bot(token=_secret_value(settings.TELEGRAM_BOT_TOKEN))
dp = Dispatcher()


class _PollingHealthMiddleware(BaseRequestMiddleware):
    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        """Observe getUpdates without changing aiogram's retry behavior."""
        if not isinstance(method, GetUpdates):
            return await make_request(bot, method)

        try:
            response = await make_request(
                bot,
                cast(TelegramMethod[TelegramType], method),
            )
        except TelegramConflictError:
            polling_health.mark_failure(conflict=True)
            raise
        except Exception:
            polling_health.mark_failure()
            raise

        polling_health.mark_success()
        return response


_track_polling_requests = _PollingHealthMiddleware()


bot.session.middleware(_track_polling_requests)


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


async def _answer_service_unavailable(message: types.Message) -> None:
    await message.answer("⚠️ Семейный сервис временно недоступен. Попробуйте ещё раз через несколько минут.")


async def setup_bot_commands(bot_instance: Bot) -> None:
    """Register the public Telegram command menu."""
    commands = [
        BotCommand(command="start", description="🌿 Запустить семейного ассистента"),
        BotCommand(command="help", description="📖 Справка и возможности"),
        BotCommand(command="oura", description="💍 Подключить Oura Ring"),
        BotCommand(command="google", description="🔗 Подключить Gmail и Calendar"),
        BotCommand(command="mail", description="✉️ Последние письма"),
        BotCommand(command="calendar", description="📅 Ближайшие события"),
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
    except Exception as error:
        logger.error("Telegram /start failed (%s).", type(error).__name__)
        await _answer_service_unavailable(message)
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
    except Exception as error:
        logger.error("Telegram /help failed (%s).", type(error).__name__)
        await _answer_service_unavailable(message)
        return

    await message.answer(
        "📖 **Доступные возможности:**\n"
        "• **/oura** — подключение Oura Ring только в личном чате\n"
        "• **/google** — подключение личных Gmail и Calendar\n"
        "• **/mail** — последние письма в личном чате\n"
        "• **/calendar** — ближайшие события в личном чате\n"
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


@dp.message(Command("google"))
async def cmd_google_setup(message: types.Message) -> None:
    try:
        async with unit_of_work() as session:
            actor = await _resolve_actor(session, message)
            IdentityService.validate_domain_access(actor, "oauth")
            raw_state, _ = await OAuthStateManager.create_state(
                session,
                user_id=actor.user_id,
                provider="google",
            )
            auth_url = GoogleOAuthClient.get_authorization_url(state=raw_state)
    except PermissionDeniedError:
        await _answer_access_denied(message)
        return
    except Exception as error:
        logger.error("Failed to initialize Google authorization (%s).", type(error).__name__)
        await message.answer("❌ Google OAuth пока не настроен. Добавьте OAuth Client ID, Secret и Redirect URI.")
        return

    await message.answer(
        "🔗 **Подключение Google:**\n\n"
        f"[Открыть защищённую страницу Google]({auth_url})\n\n"
        "Подключение выполняется отдельно для каждого семейного аккаунта. "
        "После подтверждения вернитесь в Telegram.",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


@dp.message(Command("mail"))
async def cmd_mail(message: types.Message) -> None:
    try:
        async with unit_of_work() as session:
            actor = await _resolve_actor(session, message)
            IdentityService.validate_domain_access(actor, "email")
            messages = await GoogleWorkspaceTools.list_recent_mail(
                session,
                user_id=actor.user_id,
            )
    except PermissionDeniedError:
        await _answer_access_denied(message)
        return
    except GoogleWorkspaceError:
        await message.answer("✉️ Сначала подключите личный Google-аккаунт командой /google.")
        return
    except Exception as error:
        logger.error("Gmail listing failed (%s).", type(error).__name__)
        await _answer_service_unavailable(message)
        return

    if not messages:
        await message.answer("✉️ Во входящих письмах ничего не найдено.")
        return
    lines = [f"{index}. {item['subject']}\nОт: {item['from']}" for index, item in enumerate(messages, start=1)]
    await message.answer("✉️ Последние письма:\n\n" + "\n\n".join(lines))


@dp.message(Command("calendar"))
async def cmd_calendar(message: types.Message) -> None:
    try:
        async with unit_of_work() as session:
            actor = await _resolve_actor(session, message)
            IdentityService.validate_domain_access(actor, "calendar")
            events = await GoogleWorkspaceTools.list_upcoming_events(
                session,
                user_id=actor.user_id,
            )
    except PermissionDeniedError:
        await _answer_access_denied(message)
        return
    except GoogleWorkspaceError:
        await message.answer("📅 Сначала подключите личный Google-аккаунт командой /google.")
        return
    except Exception as error:
        logger.error("Calendar listing failed (%s).", type(error).__name__)
        await _answer_service_unavailable(message)
        return

    if not events:
        await message.answer("📅 Ближайших событий не найдено.")
        return
    lines = [f"{index}. {item['summary']}\nНачало: {item['start']}" for index, item in enumerate(events, start=1)]
    await message.answer("📅 Ближайшие события:\n\n" + "\n\n".join(lines))


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
    polling_health.mark_starting()
    logger.info("Starting Telegram polling.")
    identity = await bot.get_me()
    logger.info(
        "Telegram bot identity verified: @%s (id=%d).",
        identity.username or "<none>",
        identity.id,
    )
    await bot.delete_webhook(drop_pending_updates=False)
    await setup_bot_commands(bot)
    await dp.start_polling(
        bot,
        handle_signals=False,
        close_bot_session=False,
    )
