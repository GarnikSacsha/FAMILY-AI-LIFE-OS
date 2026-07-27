import asyncio
import logging
from typing import Any

import aiohttp

from app.config.settings import settings

logger = logging.getLogger(__name__)

TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
SUPPORTED_AUDIO_TYPES = frozenset(
    {
        "audio/aac",
        "audio/flac",
        "audio/m4a",
        "audio/mp4",
        "audio/mpeg",
        "audio/ogg",
        "audio/opus",
        "audio/wav",
        "audio/webm",
        "audio/x-m4a",
        "audio/x-wav",
    }
)


class AudioTranscriptionError(Exception):
    """Safe transcription failure without provider payloads or credentials."""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class OpenAITranscriptionClient:
    @staticmethod
    def _api_key() -> str:
        value: Any = settings.OPENAI_API_KEY
        get_secret_value = getattr(value, "get_secret_value", None)
        key = get_secret_value() if callable(get_secret_value) else str(value or "")
        if not key.strip():
            raise AudioTranscriptionError(
                "Voice transcription is not configured.",
                error_code="OPENAI_NOT_CONFIGURED",
            )
        return key.strip()

    @classmethod
    async def transcribe(
        cls,
        audio_bytes: bytes,
        *,
        filename: str = "voice.ogg",
        mime_type: str = "audio/ogg",
    ) -> str:
        if not audio_bytes:
            raise AudioTranscriptionError(
                "The audio message is empty.",
                error_code="AUDIO_EMPTY",
            )
        if len(audio_bytes) > settings.AUDIO_MAX_BYTES:
            raise AudioTranscriptionError(
                "The audio message is too large.",
                error_code="AUDIO_TOO_LARGE",
            )
        normalized_type = mime_type.lower().split(";", maxsplit=1)[0].strip()
        if normalized_type not in SUPPORTED_AUDIO_TYPES:
            raise AudioTranscriptionError(
                "The audio format is not supported.",
                error_code="AUDIO_FORMAT_UNSUPPORTED",
            )

        form = aiohttp.FormData()
        form.add_field(
            "file",
            audio_bytes,
            filename=filename[:255] or "voice.ogg",
            content_type=normalized_type,
        )
        form.add_field("model", settings.AUDIO_TRANSCRIPTION_MODEL)
        form.add_field(
            "prompt",
            (
                "Family assistant voice command in Russian or Ukrainian. "
                "Preserve names, dates, times, currencies, amounts, and commands accurately."
            ),
        )
        timeout = aiohttp.ClientTimeout(total=settings.AUDIO_TRANSCRIPTION_TIMEOUT_SECONDS)
        headers = {"Authorization": f"Bearer {cls._api_key()}"}

        try:
            async with asyncio.timeout(settings.AUDIO_TRANSCRIPTION_TIMEOUT_SECONDS):
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        TRANSCRIPTIONS_URL,
                        headers=headers,
                        data=form,
                    ) as response:
                        if response.status != 200:
                            logger.warning(
                                "OpenAI transcription failed with HTTP %d.",
                                response.status,
                            )
                            raise AudioTranscriptionError(
                                "Voice transcription is temporarily unavailable.",
                                error_code=f"OPENAI_HTTP_{response.status}",
                            )
                        payload = await response.json()
        except AudioTranscriptionError:
            raise
        except TimeoutError as exc:
            raise AudioTranscriptionError(
                "Voice transcription timed out.",
                error_code="OPENAI_TRANSCRIPTION_TIMEOUT",
            ) from exc
        except (aiohttp.ClientError, ValueError) as exc:
            logger.warning("OpenAI transcription request failed (%s).", type(exc).__name__)
            raise AudioTranscriptionError(
                "Voice transcription is temporarily unavailable.",
                error_code="OPENAI_TRANSCRIPTION_FAILURE",
            ) from exc

        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise AudioTranscriptionError(
                "No speech was recognized.",
                error_code="AUDIO_NO_SPEECH",
            )
        return text.strip()
