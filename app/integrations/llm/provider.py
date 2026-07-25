from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import json
from google import genai
from google.genai import types
from app.config.settings import settings


class LLMProvider(ABC):
    """Abstract LLM Provider interface."""

    @abstractmethod
    async def generate_text(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        pass

    @abstractmethod
    async def generate_structured_json(self, prompt: str, schema: Any) -> Dict[str, Any]:
        pass


class GeminiFinanceProvider(LLMProvider):
    """Dedicated Gemini Provider for Finance Agent & Google Workspace integration."""

    def __init__(self, model_name: str = "gemini-2.5-flash"):
        self.model_name = settings.GEMINI_FINANCE_MODEL or model_name
        self.api_key = settings.GEMINI_API_KEY

    async def generate_text(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        if not self.api_key:
            return "Gemini API key is not configured."
        client = genai.Client(api_key=self.api_key)
        config = types.GenerateContentConfig(system_instruction=system_instruction) if system_instruction else None
        response = client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=config,
        )
        return response.text

    async def generate_structured_json(self, prompt: str, schema: Any) -> Dict[str, Any]:
        if not self.api_key:
            return {"category": "Uncategorized", "confidence": 0.5}
        client = genai.Client(api_key=self.api_key)
        response = client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        return json.loads(response.text)


class OpenAIProvider(LLMProvider):
    """OpenAI Provider for Orchestrator reasoning & general routing."""

    def __init__(self, model_name: str = "gpt-4o-mini"):
        self.model_name = settings.OPENAI_FAST_MODEL or model_name
        self.api_key = settings.OPENAI_API_KEY

    async def generate_text(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        if not self.api_key:
            return "OpenAI API key is not configured."
        # Using HTTP call or OpenAI SDK
        return f"[OpenAI Response for: {prompt[:50]}...]"

    async def generate_structured_json(self, prompt: str, schema: Any) -> Dict[str, Any]:
        return {}
