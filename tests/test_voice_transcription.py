import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config.settings import settings
from app.domains.identity.service import ActorContext
from app.integrations.openai.transcription import (
    AudioTranscriptionError,
    OpenAITranscriptionClient,
)
from app.telegram.bot import handle_user_message


class _Response:
    def __init__(self, *, status: int = 200, payload: object = None):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self):
        return self.payload


class _Session:
    def __init__(self, response: _Response):
        self.response = response
        self.request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def post(self, url, **kwargs):
        self.request = (url, kwargs)
        return self.response


@pytest.mark.asyncio
async def test_openai_transcription_posts_audio_in_memory(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")
    session = _Session(_Response(payload={"text": "Напомни завтра позвонить врачу"}))
    monkeypatch.setattr(
        "app.integrations.openai.transcription.aiohttp.ClientSession",
        lambda **_kwargs: session,
    )

    transcript = await OpenAITranscriptionClient.transcribe(
        b"ogg-audio",
        filename="voice.ogg",
        mime_type="audio/ogg",
    )

    assert transcript == "Напомни завтра позвонить врачу"
    assert session.request[0].endswith("/v1/audio/transcriptions")
    assert session.request[1]["headers"]["Authorization"] == "Bearer test-key"


@pytest.mark.asyncio
async def test_openai_transcription_rejects_unsupported_audio(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")
    with pytest.raises(AudioTranscriptionError) as error:
        await OpenAITranscriptionClient.transcribe(
            b"not-audio",
            filename="voice.bin",
            mime_type="application/octet-stream",
        )
    assert error.value.error_code == "AUDIO_FORMAT_UNSUPPORTED"


class _VoiceMessage:
    def __init__(self):
        self.from_user = SimpleNamespace(id=123456789, first_name="Denys")
        self.chat = SimpleNamespace(id=123456789, type="private")
        self.text = None
        self.caption = None
        self.photo = None
        self.document = None
        self.audio = None
        self.voice = SimpleNamespace(
            file_id="voice-file",
            duration=12,
            file_size=1024,
            mime_type="audio/ogg",
        )
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


@pytest.mark.asyncio
async def test_voice_message_is_transcribed_then_routed(monkeypatch):
    message = _VoiceMessage()
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

    async def download_file(_path, *, destination):
        destination.write(b"voice-bytes")

    with (
        patch("app.telegram.bot.unit_of_work", new=fake_uow),
        patch("app.telegram.bot._resolve_actor", new=AsyncMock(return_value=actor)),
        patch(
            "app.telegram.bot.bot.get_file",
            new=AsyncMock(return_value=SimpleNamespace(file_path="voice/path.ogg")),
        ),
        patch("app.telegram.bot.bot.download_file", new=AsyncMock(side_effect=download_file)),
        patch(
            "app.telegram.bot.OpenAITranscriptionClient.transcribe",
            new=AsyncMock(return_value="Сколько мы потратили сегодня?"),
        ),
        patch(
            "app.telegram.bot.MainOrchestrator.process_user_message",
            new=AsyncMock(return_value="Сегодня потратили 250 гривен."),
        ) as process_message,
    ):
        await handle_user_message(message)

    assert process_message.await_args.kwargs["message_text"] == "Сколько мы потратили сегодня?"
    assert "Сегодня потратили" in message.answers[0][0]
    assert "Распознал" not in message.answers[0][0]
    assert "Сколько мы потратили сегодня?" not in message.answers[0][0]


@pytest.mark.asyncio
async def test_voice_reminder_transcript_is_routed_without_command_rewriting(monkeypatch):
    message = _VoiceMessage()
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

    async def download_file(_path, *, destination):
        destination.write(b"voice-bytes")

    transcript = "сделай мне напоминание: завтра набрать проводницу, уточнить по поводу билетов"
    with (
        patch("app.telegram.bot.unit_of_work", new=fake_uow),
        patch("app.telegram.bot._resolve_actor", new=AsyncMock(return_value=actor)),
        patch("app.telegram.bot.bot.get_file", new=AsyncMock(return_value=SimpleNamespace(file_path="voice/path.ogg"))),
        patch("app.telegram.bot.bot.download_file", new=AsyncMock(side_effect=download_file)),
        patch("app.telegram.bot.OpenAITranscriptionClient.transcribe", new=AsyncMock(return_value=transcript)),
        patch(
            "app.telegram.bot.MainOrchestrator.process_user_message", new=AsyncMock(return_value="Готово.")
        ) as process_message,
    ):
        await handle_user_message(message)

    assert process_message.await_args.kwargs["message_text"] == transcript


@pytest.mark.asyncio
async def test_long_voice_message_is_rejected_before_download():
    message = _VoiceMessage()
    message.voice.duration = settings.AUDIO_MAX_DURATION_SECONDS + 1
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
        patch("app.telegram.bot._resolve_actor", new=AsyncMock(return_value=actor)),
        patch("app.telegram.bot.bot.get_file", new=AsyncMock()) as get_file,
    ):
        await handle_user_message(message)

    get_file.assert_not_awaited()
    assert "короче 5 минут" in message.answers[0][0]
