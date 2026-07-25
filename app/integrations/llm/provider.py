from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import json
import aiohttp
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


class TerraReasoningProvider(LLMProvider):
    """Dedicated GPT 5.6 Terra Provider for Orchestration, Health, Planner & Memory reasoning."""

    def __init__(self, model_name: str = "gpt-5.6-terra"):
        self.model_name = settings.TERRA_MODEL_NAME or settings.OPENAI_REASONING_MODEL or model_name
        self.api_key = settings.OPENAI_API_KEY

    async def generate_text(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        if not self.api_key:
            return f"Terra ({self.model_name}) response: Key not set."

        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0.7,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return f"Terra API Error ({resp.status}): {text}"
                data = await resp.json()
                return data["choices"][0]["message"]["content"]

    async def generate_structured_json(self, prompt: str, schema: Any) -> Dict[str, Any]:
        raw_text = await self.generate_text(
            prompt=prompt,
            system_instruction="You are GPT 5.6 Terra reasoning model. Output strict JSON matching the requested schema."
        )
        try:
            return json.loads(raw_text)
        except Exception:
            return {"raw_text": raw_text}


# Alias for backward compatibility
OpenAIProvider = TerraReasoningProvider
