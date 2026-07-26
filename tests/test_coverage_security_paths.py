import io
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from PIL import Image
from pydantic import SecretStr

from app.api.oauth import _rollback_if_supported, oura_oauth_callback
from app.domains.identity.service import ActorContext, PermissionDeniedError
from app.integrations.gemini import client as gemini_module
from app.integrations.gemini.client import (
    GeminiClientError,
    GeminiVisionClient,
    MealAnalysisSchema,
)
from app.security.oauth import OAuthStateError, OAuthStateManager
from app.telegram import bot as telegram_module
from app.tools.planner_tools import PlannerTools


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


def _db_session(result=None):
    session = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock(return_value=_ScalarResult(result))
    return session


@pytest.mark.asyncio
async def test_oauth_state_creation_validates_and_hashes_raw_state():
    session = _db_session()
    user_id = uuid.uuid4()

    with patch("app.security.oauth.secrets.token_urlsafe", return_value="raw-secret"):
        raw_state, record = await OAuthStateManager.create_state(
            session,
            user_id=user_id,
            provider="oura",
        )

    assert raw_state == "raw-secret"
    assert record.state_hash == OAuthStateManager._hash_state(raw_state)
    assert raw_state not in record.state_hash
    assert record.user_id == user_id
    session.add.assert_called_once_with(record)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_id", "provider"),
    [(123, "oura"), (uuid.uuid4(), ""), (uuid.uuid4(), "  ")],
)
async def test_oauth_state_creation_rejects_invalid_identity_or_provider(user_id, provider):
    with pytest.raises(OAuthStateError):
        await OAuthStateManager.create_state(
            _db_session(),
            user_id=user_id,
            provider=provider,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_state", ["", "   ", "x" * 257])
async def test_oauth_state_rejects_malformed_state_before_database(raw_state):
    session = _db_session()
    with pytest.raises(OAuthStateError):
        await OAuthStateManager.validate_and_consume_state(
            session,
            raw_state=raw_state,
        )
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_oauth_state_rejects_blank_provider_before_database():
    session = _db_session()
    with pytest.raises(OAuthStateError):
        await OAuthStateManager.validate_and_consume_state(
            session,
            raw_state="state",
            provider=" ",
        )
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("record_kind", ["missing", "consumed", "expired"])
async def test_oauth_state_rejects_missing_replayed_and_expired_records(record_kind):
    now = datetime.now(timezone.utc)
    if record_kind == "missing":
        record = None
    else:
        record = SimpleNamespace(
            user_id=uuid.uuid4(),
            consumed_at=now if record_kind == "consumed" else None,
            expires_at=(
                (now - timedelta(seconds=1)).replace(tzinfo=None)
                if record_kind == "expired"
                else now + timedelta(minutes=1)
            ),
        )
    session = _db_session(record)

    with pytest.raises(OAuthStateError):
        await OAuthStateManager.validate_and_consume_state(
            session,
            raw_state="state",
        )


@pytest.mark.asyncio
async def test_oauth_state_consumes_valid_record_once():
    user_id = uuid.uuid4()
    record = SimpleNamespace(
        user_id=user_id,
        consumed_at=None,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    session = _db_session(record)

    resolved_user_id = await OAuthStateManager.validate_and_consume_state(
        session,
        raw_state="state",
    )

    assert resolved_user_id == user_id
    assert record.consumed_at is not None
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_oauth_callback_handles_provider_cancel_and_missing_parameters():
    cancelled = await oura_oauth_callback(
        session=object(),
        code=None,
        state=None,
        error="access_denied",
    )
    assert cancelled.status_code == 400

    with pytest.raises(HTTPException) as caught:
        await oura_oauth_callback(
            session=object(),
            code=None,
            state="state",
            error=None,
        )
    assert caught.value.status_code == 400


@pytest.mark.asyncio
async def test_oauth_callback_rolls_back_unexpected_provider_failure():
    session = AsyncMock()
    with (
        patch(
            "app.api.oauth.OAuthStateManager.validate_and_consume_state",
            new=AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            "app.api.oauth.OuraClient.exchange_code_for_tokens",
            new=AsyncMock(side_effect=RuntimeError("provider failed")),
        ),
    ):
        response = await oura_oauth_callback(
            session=session,
            code="code",
            state="state",
            error=None,
        )

    assert response.status_code == 500
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_rollback_helper_accepts_absent_and_synchronous_rollback():
    await _rollback_if_supported(object())
    session = SimpleNamespace(rollback=MagicMock(return_value=None))
    await _rollback_if_supported(session)
    session.rollback.assert_called_once()


def test_gemini_secret_and_upload_validation_branches():
    assert gemini_module._secret_value(None) == ""
    assert gemini_module._secret_value(SecretStr("key")) == "key"

    cases = [
        (b"", "image/png", "IMAGE_EMPTY"),
        (
            b"x" * (GeminiVisionClient.MAX_IMAGE_SIZE_BYTES + 1),
            "image/png",
            "IMAGE_TOO_LARGE",
        ),
        (_png_bytes(), "application/pdf", "IMAGE_TYPE_UNSUPPORTED"),
    ]
    for payload, mime_type, error_code in cases:
        with pytest.raises(GeminiClientError) as caught:
            GeminiVisionClient._verify_image(payload, mime_type)
        assert caught.value.error_code == error_code


@pytest.mark.asyncio
async def test_gemini_valid_image_fails_closed_without_api_key(monkeypatch):
    monkeypatch.setattr(gemini_module.settings, "GEMINI_API_KEY", None)
    with pytest.raises(GeminiClientError) as caught:
        await GeminiVisionClient.analyze_food_photo(_png_bytes(), "image/png")
    assert caught.value.error_code == "GEMINI_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_gemini_fails_closed_without_sdk(monkeypatch):
    monkeypatch.setattr(gemini_module.settings, "GEMINI_API_KEY", "key")
    monkeypatch.setattr(gemini_module, "genai", None)
    monkeypatch.setattr(gemini_module, "types", None)

    with pytest.raises(GeminiClientError) as caught:
        await GeminiVisionClient.analyze_food_photo(_png_bytes(), "image/png")
    assert caught.value.error_code == "GEMINI_SDK_UNAVAILABLE"


class _FakePart:
    @staticmethod
    def from_bytes(**kwargs):
        return kwargs


class _FakeTypes:
    Part = _FakePart

    @staticmethod
    def GenerateContentConfig(**kwargs):
        return kwargs


def _install_fake_gemini(monkeypatch, *, result=None, side_effect=None, close_error=False):
    generate = AsyncMock(return_value=result, side_effect=side_effect)
    async_client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
    async_client.aclose = AsyncMock(side_effect=RuntimeError("close failed") if close_error else None)
    client = SimpleNamespace(aio=async_client)
    fake_genai = SimpleNamespace(Client=MagicMock(return_value=client))
    monkeypatch.setattr(gemini_module.settings, "GEMINI_API_KEY", "key")
    monkeypatch.setattr(gemini_module, "genai", fake_genai)
    monkeypatch.setattr(gemini_module, "types", _FakeTypes)
    return generate, async_client


def _valid_meal() -> dict:
    return {
        "dish_name": "Salad",
        "ingredients": ["greens"],
        "calories_est": 100,
        "proteins_g": 3,
        "fats_g": 4,
        "carbs_g": 12,
        "confidence_score": 0.8,
        "coaching_tip": "Estimate only",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("response_kind", ["schema", "dict", "json"])
async def test_gemini_accepts_all_supported_structured_responses(monkeypatch, response_kind):
    meal = _valid_meal()
    if response_kind == "schema":
        response = SimpleNamespace(
            parsed=MealAnalysisSchema.model_validate(meal),
            text=None,
        )
    elif response_kind == "dict":
        response = SimpleNamespace(parsed=meal, text=None)
    else:
        response = SimpleNamespace(parsed=None, text=json.dumps(meal))
    _, async_client = _install_fake_gemini(monkeypatch, result=response)

    result = await GeminiVisionClient.analyze_food_photo(
        _png_bytes(),
        "image/png",
    )

    assert result["dish_name"] == "Salad"
    async_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "side_effect", "error_code"),
    [
        (SimpleNamespace(parsed=None, text=None), None, "GEMINI_EMPTY_RESPONSE"),
        (SimpleNamespace(parsed={"dish_name": ""}, text=None), None, "GEMINI_RESPONSE_INVALID"),
        (None, TimeoutError(), "GEMINI_TIMEOUT"),
        (None, RuntimeError("provider"), "GEMINI_PROVIDER_FAILURE"),
    ],
)
async def test_gemini_maps_empty_invalid_timeout_and_provider_failures(monkeypatch, result, side_effect, error_code):
    _install_fake_gemini(
        monkeypatch,
        result=result,
        side_effect=side_effect,
    )
    with pytest.raises(GeminiClientError) as caught:
        await GeminiVisionClient.analyze_food_photo(
            _png_bytes(),
            "image/png",
        )
    assert caught.value.error_code == error_code


@pytest.mark.asyncio
async def test_gemini_cleanup_failure_does_not_hide_valid_result(monkeypatch):
    response = SimpleNamespace(parsed=_valid_meal(), text=None)
    _install_fake_gemini(
        monkeypatch,
        result=response,
        close_error=True,
    )
    result = await GeminiVisionClient.analyze_food_photo(
        _png_bytes(),
        "image/png",
    )
    assert result["dish_name"] == "Salad"


class _Message:
    def __init__(self, *, sender=True, text="hello", photo=None):
        self.from_user = SimpleNamespace(id=123456789, first_name="Denys") if sender else None
        self.chat = SimpleNamespace(id=123456789, type="private")
        self.text = text
        self.caption = None
        self.photo = photo
        self.document = None
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


def _actor() -> ActorContext:
    return ActorContext(
        user_id=uuid.uuid4(),
        telegram_id=123456789,
        household_id=uuid.uuid4(),
        chat_id=123456789,
        chat_type="private",
    )


@asynccontextmanager
async def _fake_uow():
    yield object()


@pytest.mark.asyncio
async def test_telegram_rejects_senderless_update_and_permission_denial():
    senderless = _Message(sender=False)
    await telegram_module.handle_user_message(senderless)
    assert senderless.answers

    denied = _Message()
    with (
        patch("app.telegram.bot.unit_of_work", new=_fake_uow),
        patch(
            "app.telegram.bot.IdentityService.resolve_actor",
            new=AsyncMock(side_effect=PermissionDeniedError("denied")),
        ),
    ):
        await telegram_module.handle_user_message(denied)
    assert denied.answers


@pytest.mark.asyncio
async def test_telegram_oura_setup_handles_denial_and_internal_failure():
    denied = _Message(text="/oura")
    failed = _Message(text="/oura")
    with (
        patch("app.telegram.bot.unit_of_work", new=_fake_uow),
        patch(
            "app.telegram.bot.IdentityService.resolve_actor",
            new=AsyncMock(side_effect=PermissionDeniedError("denied")),
        ),
    ):
        await telegram_module.cmd_oura_setup(denied)
    with (
        patch("app.telegram.bot.unit_of_work", new=_fake_uow),
        patch(
            "app.telegram.bot.IdentityService.resolve_actor",
            new=AsyncMock(return_value=_actor()),
        ),
        patch(
            "app.telegram.bot.OAuthStateManager.create_state",
            new=AsyncMock(side_effect=RuntimeError("db")),
        ),
    ):
        await telegram_module.cmd_oura_setup(failed)
    assert denied.answers and failed.answers


@pytest.mark.asyncio
async def test_telegram_photo_download_failure_is_safe():
    message = _Message(
        photo=[SimpleNamespace(file_id="photo")],
        text="food",
    )
    with (
        patch("app.telegram.bot.unit_of_work", new=_fake_uow),
        patch(
            "app.telegram.bot.IdentityService.resolve_actor",
            new=AsyncMock(return_value=_actor()),
        ),
        patch(
            "app.telegram.bot.bot.get_file",
            new=AsyncMock(return_value=SimpleNamespace(file_path=None)),
        ),
    ):
        await telegram_module.handle_user_message(message)
    assert message.answers


@pytest.mark.asyncio
async def test_planner_create_task_and_active_list_paths():
    session = MagicMock()
    session.flush = AsyncMock()
    creator_id = uuid.uuid4()
    result = await PlannerTools.create_task(
        session,
        creator_id=creator_id,
        owner_type="user",
        owner_id=creator_id,
        title="Task",
    )
    assert result["status"] == "CREATED"

    item = SimpleNamespace(id=uuid.uuid4(), item_name="Milk", quantity="1")
    query_result = MagicMock()
    query_result.scalars.return_value.all.return_value = [item]
    session.execute = AsyncMock(return_value=query_result)
    items = await PlannerTools.get_active_shopping_list(
        session,
        household_id=uuid.uuid4(),
    )
    assert items == [{"id": str(item.id), "item_name": "Milk", "quantity": "1"}]
