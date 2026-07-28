import io
import logging
import time
import uuid
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
from app.integrations.openai.transcription import (
    AudioTranscriptionError,
    OpenAITranscriptionClient,
)
from app.integrations.oura.client import OuraClient
from app.orchestration.orchestrator import MainOrchestrator
from app.security.oauth import OAuthStateManager
from app.tools.google_tools import GoogleWorkspaceError, GoogleWorkspaceTools
from app.tools.health_tools import HealthTools
from app.tools.memory_tools import SharedMemoryTools

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


def _escape_markdown_text(value: str) -> str:
    for character in ("\\", "*", "_", "[", "]", "`"):
        value = value.replace(character, f"\\{character}")
    return value


def _message_audio(message: types.Message) -> tuple[Any, str, str] | None:
    voice = getattr(message, "voice", None)
    audio = getattr(message, "audio", None)
    if voice is not None:
        return voice, "voice.ogg", voice.mime_type or "audio/ogg"
    if audio is not None:
        return (
            audio,
            audio.file_name or "audio.mp3",
            audio.mime_type or "audio/mpeg",
        )
    return None


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
        "• напишите «напомни завтра в 10:00…» — пришлю напоминание прямо в этот чат\n"
        "• отправьте фото еды для приблизительной оценки состава",
        parse_mode=ParseMode.MARKDOWN,
    )


@dp.message(Command("oura"))
async def cmd_oura_setup(message: types.Message) -> None:
    auth_url: str | None = None
    command_parts = (message.text or "").strip().lower().split(maxsplit=1)
    force_reconnect = len(command_parts) == 2 and command_parts[1] in {
        "reconnect",
        "переподключить",
    }
    try:
        async with unit_of_work() as session:
            actor = await _resolve_actor(session, message)
            IdentityService.validate_domain_access(actor, "oauth")
            connection = await HealthTools.get_oura_connection_status(
                session,
                user_id=actor.user_id,
            )
            if force_reconnect or not connection["connected"]:
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

    if auth_url is None:
        await message.answer(
            "💍 Oura Ring уже подключена к вашему личному аккаунту.\n"
            "Чтобы получить сегодняшнюю сводку, отправьте /health "
            "или спросите: «Как я сегодня спал по Oura?»\n"
            "Если доступ был отозван, используйте /oura reconnect."
        )
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
    except GoogleWorkspaceError as error:
        if error.error_code == "GOOGLE_CALENDAR_SCOPE_MISSING":
            await message.answer(
                "📅 Почта подключена, но разрешения на Calendar в текущем токене нет. "
                "Переподключите Google командой /google и подтвердите доступ к календарю."
            )
        elif error.error_code == "GOOGLE_PERMISSION_OR_API_DISABLED":
            await message.answer(
                "📅 Google-аккаунт подключён, но Calendar API отклонил запрос. "
                "Проверьте, что Google Calendar API включён в Google Cloud Console, "
                "затем выполните /google ещё раз."
            )
        else:
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
    transcript: str | None = None
    shared_response_coordinates: tuple[uuid.UUID, int] | None = None

    try:
        async with unit_of_work() as session:
            actor = await _resolve_actor(session, message)

            audio_attachment = _message_audio(message)
            if audio_attachment is not None:
                audio, filename, mime_type = audio_attachment
                if audio.duration > settings.AUDIO_MAX_DURATION_SECONDS:
                    raise AudioTranscriptionError(
                        "The audio message is too long.",
                        error_code="AUDIO_TOO_LONG",
                    )
                if audio.file_size is not None and audio.file_size > settings.AUDIO_MAX_BYTES:
                    raise AudioTranscriptionError(
                        "The audio message is too large.",
                        error_code="AUDIO_TOO_LARGE",
                    )
                file_info = await bot.get_file(audio.file_id)
                if not file_info.file_path:
                    raise AudioTranscriptionError(
                        "Telegram returned an empty audio path.",
                        error_code="TELEGRAM_AUDIO_UNAVAILABLE",
                    )
                audio_buffer = io.BytesIO()
                await bot.download_file(file_info.file_path, destination=audio_buffer)
                transcript = await OpenAITranscriptionClient.transcribe(
                    audio_buffer.getvalue(),
                    filename=filename,
                    mime_type=mime_type,
                )
                text = transcript

            photo_bytes = None
            if message.photo:
                photo = message.photo[-1]
                file_info = await bot.get_file(photo.file_id)
                if not file_info.file_path:
                    raise ValueError("Telegram returned an empty photo path.")
                photo_buffer = io.BytesIO()
                await bot.download_file(file_info.file_path, destination=photo_buffer)
                photo_bytes = photo_buffer.getvalue()

            shared_context_enabled = (
                settings.SHARED_CHAT_MEMORY_ENABLED
                and actor.chat_type in {"group", "supergroup"}
            )

            domain = MainOrchestrator.domain_for_message(
                text,
                has_photo=bool(message.photo),
                has_document=bool(message.document),
            )
            if (
                shared_context_enabled
                and message.photo
                and domain == "health"
            ):
                # A food photo intentionally posted in the authorized family
                # group may be analyzed, but it is not added to personal health
                # history or used as private context.
                domain = "general"
            IdentityService.validate_domain_access(actor, domain)
            if shared_context_enabled:
                if transcript is not None:
                    message_type = "voice"
                elif message.photo:
                    message_type = "photo"
                elif message.document:
                    message_type = "document"
                else:
                    message_type = "text"
                await SharedMemoryTools.record_message(
                    session,
                    household_id=actor.household_id,
                    author_user_id=actor.user_id,
                    telegram_chat_id=actor.chat_id,
                    telegram_message_id=getattr(message, "message_id", None),
                    role="user",
                    author_name=user_name,
                    message_type=message_type,
                    content=text or f"[{message_type}]",
                )

            response_text = await MainOrchestrator.process_user_message(
                session=session,
                user_id=actor.user_id,
                household_id=actor.household_id,
                user_name=user_name,
                message_text=text,
                photo_bytes=photo_bytes,
                timezone_name=actor.timezone,
                telegram_chat_id=actor.chat_id,
                shared_context_enabled=shared_context_enabled,
            )
            if shared_context_enabled:
                shared_response_coordinates = (
                    actor.household_id,
                    actor.chat_id,
                )
    except PermissionDeniedError:
        await _answer_access_denied(message)
        return
    except AudioTranscriptionError as error:
        if error.error_code in {"AUDIO_TOO_LONG", "AUDIO_TOO_LARGE"}:
            await message.answer("🎙 Голосовое слишком длинное или большое. Отправьте запись короче 5 минут.")
        elif error.error_code == "OPENAI_NOT_CONFIGURED":
            await message.answer("🎙 Распознавание голоса пока не настроено: отсутствует OPENAI_API_KEY.")
        elif error.error_code == "AUDIO_NO_SPEECH":
            await message.answer("🎙 Не смог разобрать речь. Попробуйте записать голосовое ещё раз.")
        else:
            await message.answer("🎙 Сейчас не удалось распознать голосовое. Попробуйте немного позже.")
        return
    except Exception:
        # Never echo provider payloads, authorization codes, or traceback text.
        logger.error("Telegram update failed before commit.")
        await message.answer("❌ Не удалось выполнить запрос. Изменения не были сохранены.")
        return

    # A success response is sent only after the Unit of Work has committed.
    if transcript is not None:
        response_text = f"🎙 Распознал: «{_escape_markdown_text(transcript)}»\n\n{response_text}"
    sent_message = await message.answer(response_text, parse_mode=ParseMode.MARKDOWN)
    if shared_response_coordinates is not None:
        household_id, chat_id = shared_response_coordinates
        try:
            async with unit_of_work() as session:
                await SharedMemoryTools.record_message(
                    session,
                    household_id=household_id,
                    author_user_id=None,
                    telegram_chat_id=chat_id,
                    telegram_message_id=getattr(sent_message, "message_id", None),
                    role="assistant",
                    author_name="Family",
                    message_type="text",
                    content=response_text,
                )
        except Exception as error:
            logger.warning(
                "Telegram response was delivered but shared context persistence failed (%s).",
                type(error).__name__,
            )


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
