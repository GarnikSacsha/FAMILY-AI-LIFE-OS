import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import aiohttp
from google import genai
from google.genai import types

from app.config.settings import settings

logger = logging.getLogger(__name__)


def _secret_value(value: Any) -> str:
    if value is None:
        return ""
    get_secret_value = getattr(value, "get_secret_value", None)
    return get_secret_value() if callable(get_secret_value) else str(value)


class LLMProviderError(Exception):
    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class LLMProvider(ABC):
    @abstractmethod
    async def generate_text(self, prompt: str, system_instruction: str | None = None) -> str:
        raise NotImplementedError

    @abstractmethod
    async def generate_structured_json(self, prompt: str, schema: Any) -> dict[str, Any]:
        raise NotImplementedError


class GeminiFinanceProvider(LLMProvider):
    def __init__(self, model_name: str = "gemini-2.5-flash"):
        self.model_name = settings.GEMINI_FINANCE_MODEL or model_name
        self.api_key = _secret_value(settings.GEMINI_API_KEY).strip()

    def _require_api_key(self) -> str:
        if not self.api_key:
            raise LLMProviderError(
                "Finance categorization is temporarily unavailable.",
                error_code="GEMINI_NOT_CONFIGURED",
            )
        return self.api_key

    async def _generate(self, *, prompt: str, config: Any = None) -> Any:
        client: Any = None
        async_client: Any = None
        try:
            client = genai.Client(api_key=self._require_api_key())
            async_client = client.aio
            async with asyncio.timeout(float(getattr(settings, "GEMINI_TIMEOUT_SECONDS", 15.0))):
                return await async_client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config,
                )
        except LLMProviderError:
            raise
        except TimeoutError as exc:
            raise LLMProviderError(
                "Finance categorization timed out.",
                error_code="GEMINI_TIMEOUT",
            ) from exc
        except Exception as exc:
            logger.warning(
                "Gemini finance request failed (%s)",
                type(exc).__name__,
                extra={"error_code": "GEMINI_PROVIDER_FAILURE"},
            )
            raise LLMProviderError(
                "Finance categorization is temporarily unavailable.",
                error_code="GEMINI_PROVIDER_FAILURE",
            ) from exc
        finally:
            close = getattr(async_client, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:
                    logger.warning(
                        "Gemini finance client cleanup failed",
                        extra={"error_code": "GEMINI_CLIENT_CLOSE_FAILURE"},
                    )

    async def generate_text(self, prompt: str, system_instruction: str | None = None) -> str:
        config = types.GenerateContentConfig(system_instruction=system_instruction) if system_instruction else None
        response = await self._generate(prompt=prompt, config=config)
        text = getattr(response, "text", None)
        if not text:
            raise LLMProviderError(
                "Finance categorization returned an empty response.",
                error_code="GEMINI_EMPTY_RESPONSE",
            )
        return text

    async def generate_structured_json(self, prompt: str, schema: Any) -> dict[str, Any]:
        response = await self._generate(
            prompt=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            if hasattr(parsed, "model_dump"):
                return parsed.model_dump()
            if isinstance(parsed, dict):
                return parsed
        text = getattr(response, "text", None)
        if not text:
            raise LLMProviderError(
                "Finance categorization returned an empty response.",
                error_code="GEMINI_EMPTY_RESPONSE",
            )
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(
                "Finance categorization returned invalid JSON.",
                error_code="GEMINI_RESPONSE_INVALID",
            ) from exc
        if not isinstance(value, dict):
            raise LLMProviderError(
                "Finance categorization returned an invalid response.",
                error_code="GEMINI_RESPONSE_INVALID",
            )
        return value


class TerraReasoningProvider(LLMProvider):
    def __init__(self, model_name: str = "gpt-5.6-terra"):
        self.model_name = settings.TERRA_MODEL_NAME or settings.OPENAI_REASONING_MODEL or model_name
        self.api_key = _secret_value(settings.OPENAI_API_KEY).strip()

    async def generate_text(self, prompt: str, system_instruction: str | None = None) -> str:
        if not self.api_key:
            raise LLMProviderError(
                "Reasoning service is temporarily unavailable.",
                error_code="OPENAI_NOT_CONFIGURED",
            )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model_name,
            "input": prompt,
            "store": False,
        }
        if system_instruction:
            payload["instructions"] = system_instruction
        timeout = aiohttp.ClientTimeout(total=float(getattr(settings, "OPENAI_TIMEOUT_SECONDS", 20.0)))

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    "https://api.openai.com/v1/responses",
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status != 200:
                        raise LLMProviderError(
                            "Reasoning service is temporarily unavailable.",
                            error_code=f"OPENAI_HTTP_{response.status}",
                        )
                    data = await response.json()
            for item in data.get("output", []):
                if item.get("type") != "message":
                    continue
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        text = content.get("text")
                        if isinstance(text, str) and text.strip():
                            return text.strip()
            raise LLMProviderError(
                "Reasoning service returned an empty response.",
                error_code="OPENAI_RESPONSE_INVALID",
            )
        except LLMProviderError:
            raise
        except (TimeoutError, aiohttp.ClientError, AttributeError, KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Reasoning provider request failed (%s)",
                type(exc).__name__,
                extra={"error_code": "OPENAI_PROVIDER_FAILURE"},
            )
            raise LLMProviderError(
                "Reasoning service is temporarily unavailable.",
                error_code="OPENAI_PROVIDER_FAILURE",
            ) from exc

    async def generate_structured_json(self, prompt: str, schema: Any) -> dict[str, Any]:
        raw_text = await self.generate_text(
            prompt=prompt,
            system_instruction="Return strict JSON matching the requested schema.",
        )
        try:
            result = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(
                "Reasoning service returned invalid JSON.",
                error_code="OPENAI_RESPONSE_INVALID",
            ) from exc
        if not isinstance(result, dict):
            raise LLMProviderError(
                "Reasoning service returned an invalid response.",
                error_code="OPENAI_RESPONSE_INVALID",
            )
        return result


OpenAIProvider = TerraReasoningProvider
