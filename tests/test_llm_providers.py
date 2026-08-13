import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from pydantic import SecretStr

from app.agents.finance.agent import FinanceAgent
from app.config.settings import settings
from app.integrations.llm.provider import (
    GeminiFinanceProvider,
    LLMProviderError,
    TerraReasoningProvider,
)
from app.tools.finance_tools import FinanceTools


class FakeGeminiAsyncClient:
    def __init__(self, response=None, error=None, close_error=None):
        self.models = SimpleNamespace(generate_content=AsyncMock(return_value=response, side_effect=error))
        self.close_error = close_error
        self.closed = False

    async def aclose(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


class FakeGeminiClient:
    async_client = None
    api_keys = []

    def __init__(self, *, api_key):
        type(self).api_keys.append(api_key)
        self.aio = type(self).async_client


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = payload

    async def json(self):
        return self.payload


class ResponseContext:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self.response

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    response_context = None

    def __init__(self, *, timeout):
        self.timeout = timeout
        self.post_args = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def post(self, *args, **kwargs):
        self.post_args = (args, kwargs)
        return type(self).response_context


def test_secret_and_provider_error_helpers():
    from app.integrations.llm.provider import _secret_value

    assert _secret_value(None) == ""
    assert _secret_value(SecretStr("secret")) == "secret"
    assert _secret_value(123) == "123"
    error = LLMProviderError("message", error_code="CODE")
    assert str(error) == "message"
    assert error.error_code == "CODE"


@pytest.mark.asyncio
async def test_gemini_text_request_closes_client(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", SecretStr("gemini-key"))
    monkeypatch.setattr(settings, "GEMINI_FINANCE_MODEL", "test-model")
    async_client = FakeGeminiAsyncClient(response=SimpleNamespace(text="answer"))
    FakeGeminiClient.async_client = async_client
    FakeGeminiClient.api_keys.clear()
    monkeypatch.setattr("app.integrations.llm.provider.genai.Client", FakeGeminiClient)

    result = await GeminiFinanceProvider().generate_text("prompt", "system")

    assert result == "answer"
    assert FakeGeminiClient.api_keys == ["gemini-key"]
    assert async_client.closed
    assert async_client.models.generate_content.await_args.kwargs["model"] == "test-model"


@pytest.mark.asyncio
async def test_gemini_maps_provider_failures_and_cleanup_failures(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", SecretStr("gemini-key"))
    async_client = FakeGeminiAsyncClient(error=RuntimeError("upstream"), close_error=RuntimeError("close"))
    FakeGeminiClient.async_client = async_client
    monkeypatch.setattr("app.integrations.llm.provider.genai.Client", FakeGeminiClient)

    with pytest.raises(LLMProviderError, match="temporarily unavailable") as caught:
        await GeminiFinanceProvider().generate_text("prompt")

    assert caught.value.error_code == "GEMINI_PROVIDER_FAILURE"
    assert async_client.closed


@pytest.mark.asyncio
async def test_gemini_maps_timeout_and_missing_key(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", SecretStr("gemini-key"))
    async_client = FakeGeminiAsyncClient(error=TimeoutError())
    FakeGeminiClient.async_client = async_client
    monkeypatch.setattr("app.integrations.llm.provider.genai.Client", FakeGeminiClient)

    with pytest.raises(LLMProviderError) as timeout_error:
        await GeminiFinanceProvider().generate_text("prompt")
    assert timeout_error.value.error_code == "GEMINI_TIMEOUT"

    monkeypatch.setattr(settings, "GEMINI_API_KEY", SecretStr(""))
    with pytest.raises(LLMProviderError) as missing_key:
        await GeminiFinanceProvider().generate_text("prompt")
    assert missing_key.value.error_code == "GEMINI_NOT_CONFIGURED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            SimpleNamespace(parsed=SimpleNamespace(model_dump=lambda: {"category": "Groceries"})),
            {"category": "Groceries"},
        ),
        (SimpleNamespace(parsed={"category": "Health"}), {"category": "Health"}),
        (SimpleNamespace(parsed=None, text='{"category": "Transport"}'), {"category": "Transport"}),
    ],
)
async def test_gemini_structured_response_shapes(monkeypatch, response, expected):
    provider = GeminiFinanceProvider.__new__(GeminiFinanceProvider)
    provider._generate = AsyncMock(return_value=response)

    assert await provider.generate_structured_json("prompt", object) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error_code"),
    [
        (SimpleNamespace(parsed=None, text=None), "GEMINI_EMPTY_RESPONSE"),
        (SimpleNamespace(parsed=None, text="not-json"), "GEMINI_RESPONSE_INVALID"),
        (SimpleNamespace(parsed=None, text="[]"), "GEMINI_RESPONSE_INVALID"),
    ],
)
async def test_gemini_rejects_empty_or_invalid_structured_response(response, error_code):
    provider = GeminiFinanceProvider.__new__(GeminiFinanceProvider)
    provider._generate = AsyncMock(return_value=response)

    with pytest.raises(LLMProviderError) as caught:
        await provider.generate_structured_json("prompt", object)
    assert caught.value.error_code == error_code


@pytest.mark.asyncio
async def test_gemini_rejects_empty_text(monkeypatch):
    provider = GeminiFinanceProvider.__new__(GeminiFinanceProvider)
    provider._generate = AsyncMock(return_value=SimpleNamespace(text=""))

    with pytest.raises(LLMProviderError) as caught:
        await provider.generate_text("prompt")
    assert caught.value.error_code == "GEMINI_EMPTY_RESPONSE"


@pytest.mark.asyncio
async def test_terra_text_request_and_structured_json(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", SecretStr("openai-key"))
    FakeSession.response_context = ResponseContext(
        FakeResponse(
            payload={
                "output": [
                    {"type": "reasoning", "content": []},
                    {"type": "message", "content": [{"type": "output_text", "text": "  answer  "}]},
                ]
            }
        )
    )
    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)
    provider = TerraReasoningProvider(model_name="test-model")

    assert await provider.generate_text("prompt", "system") == "answer"


@pytest.mark.asyncio
async def test_terra_structured_json_parsing(monkeypatch):
    provider = TerraReasoningProvider.__new__(TerraReasoningProvider)
    provider.generate_text = AsyncMock(return_value=json.dumps({"ok": True}))
    assert await provider.generate_structured_json("prompt", object) == {"ok": True}

    provider.generate_text = AsyncMock(return_value="[]")
    with pytest.raises(LLMProviderError) as caught:
        await provider.generate_structured_json("prompt", object)
    assert caught.value.error_code == "OPENAI_RESPONSE_INVALID"

    provider.generate_text = AsyncMock(return_value="not-json")
    with pytest.raises(LLMProviderError) as caught:
        await provider.generate_structured_json("prompt", object)
    assert caught.value.error_code == "OPENAI_RESPONSE_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_context", "error_code"),
    [
        (ResponseContext(FakeResponse(status=500)), "OPENAI_HTTP_500"),
        (ResponseContext(FakeResponse(payload={"output": []})), "OPENAI_RESPONSE_INVALID"),
        (ResponseContext(error=aiohttp.ClientError("network")), "OPENAI_PROVIDER_FAILURE"),
    ],
)
async def test_terra_maps_http_empty_and_network_failures(monkeypatch, response_context, error_code):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", SecretStr("openai-key"))
    FakeSession.response_context = response_context
    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)

    with pytest.raises(LLMProviderError) as caught:
        await TerraReasoningProvider().generate_text("prompt")
    assert caught.value.error_code == error_code


@pytest.mark.asyncio
async def test_terra_rejects_missing_key(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", SecretStr(""))
    with pytest.raises(LLMProviderError) as caught:
        await TerraReasoningProvider().generate_text("prompt")
    assert caught.value.error_code == "OPENAI_NOT_CONFIGURED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_result", "expected_category"),
    [
        ({"category": "Health"}, "Health"),
        ({"category": "Not a category"}, "Groceries"),
    ],
)
async def test_finance_agent_uses_valid_category_or_deterministic_fallback(
    provider_result, expected_category, monkeypatch
):
    agent = FinanceAgent.__new__(FinanceAgent)
    agent.provider = SimpleNamespace(generate_structured_json=AsyncMock(return_value=provider_result))
    logged = {"status": "SUCCESS", "category": expected_category}
    log_transaction = AsyncMock(return_value=logged)
    monkeypatch.setattr(FinanceTools, "log_transaction", log_transaction)

    result = await agent.categorize_and_log_transaction(
        session=object(),
        owner_type="household",
        owner_id=object(),
        amount=212,
        merchant="продукты",
        description="продукты",
        external_id="expense-1",
    )

    assert result == logged
    assert log_transaction.await_args.kwargs["category"] == expected_category
    assert log_transaction.await_args.kwargs["external_id"] == "expense-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expense_label",
    [
        "оплата квартиры",
        "ежемесячные платежи (Gemini Pro)",
        "ежемесячные платежи (Apple iCloud)",
        "ежемесячные платежи (Claude)",
    ],
)
async def test_explicit_monthly_payment_label_overrides_model_category(expense_label, monkeypatch):
    agent = FinanceAgent.__new__(FinanceAgent)
    agent.provider = SimpleNamespace(
        generate_structured_json=AsyncMock(return_value={"category": "Shopping"})
    )
    log_transaction = AsyncMock(return_value={"status": "SUCCESS", "category": "Utilities"})
    monkeypatch.setattr(FinanceTools, "log_transaction", log_transaction)

    await agent.categorize_and_log_transaction(
        session=object(),
        owner_type="household",
        owner_id=object(),
        amount=990,
        merchant=expense_label,
        description=f"990 грн — {expense_label}",
    )

    assert log_transaction.await_args.kwargs["category"] == "Utilities"


@pytest.mark.asyncio
async def test_finance_agent_falls_back_when_categorization_fails(monkeypatch):
    agent = FinanceAgent.__new__(FinanceAgent)
    agent.provider = SimpleNamespace(
        generate_structured_json=AsyncMock(side_effect=RuntimeError("provider unavailable"))
    )
    log_transaction = AsyncMock(return_value={"status": "SUCCESS"})
    monkeypatch.setattr(FinanceTools, "log_transaction", log_transaction)

    await agent.categorize_and_log_transaction(
        session=object(),
        owner_type="user",
        owner_id=object(),
        amount=100,
        merchant="unknown",
    )

    assert log_transaction.await_args.kwargs["category"] == "Uncategorized"


@pytest.mark.asyncio
async def test_finance_agent_generates_report_from_spending_summary(monkeypatch):
    agent = FinanceAgent.__new__(FinanceAgent)
    agent.provider = SimpleNamespace(generate_text=AsyncMock(return_value="report"))
    summary = {"currencies": {"UAH": {"total_expense": "212.00"}}}
    monkeypatch.setattr(FinanceTools, "get_spending_summary", AsyncMock(return_value=summary))

    result = await agent.generate_financial_report(object(), object())

    assert result == "report"
    assert agent.provider.generate_text.await_args.args[0].find("212.00") >= 0
