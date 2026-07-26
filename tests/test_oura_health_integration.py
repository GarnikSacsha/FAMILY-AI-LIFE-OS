import base64
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from app.config.settings import settings
from app.domains.identity.service import ActorContext
from app.integrations.oura.client import OuraClient, OuraOAuthError
from app.orchestration.orchestrator import MainOrchestrator
from app.security.token_cipher import TokenCipher
from app.telegram import bot as telegram_module
from app.tools.health_tools import HealthIntegrationError, HealthTools


class _Message:
    def __init__(self, text: str = "/oura"):
        self.from_user = SimpleNamespace(id=123456789, first_name="Denys")
        self.chat = SimpleNamespace(id=123456789, type="private")
        self.text = text
        self.answers: list[tuple[str, dict]] = []

    async def answer(self, text: str, **kwargs) -> None:
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


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


def _session_returning(record=None):
    session = MagicMock()
    session.execute = AsyncMock(return_value=_ScalarResult(record))
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


def _cipher() -> TokenCipher:
    return TokenCipher(base64.b64encode(b"o" * 32).decode("ascii"))


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False


class _HttpResponse:
    def __init__(self, *, status=200, payload=None):
        self.status = status
        self.payload = payload if payload is not None else {}

    async def json(self):
        return self.payload


class _HttpSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return _AsyncContext(self.response)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return _AsyncContext(self.response)


def _configure_oura(monkeypatch):
    monkeypatch.setattr(settings, "OURA_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "OURA_CLIENT_SECRET", SecretStr("client-secret"))
    monkeypatch.setattr(
        settings,
        "OURA_REDIRECT_URI",
        "https://family.example.com/oauth/oura/callback",
    )


@pytest.mark.asyncio
async def test_connected_oura_status_is_deterministic_not_llm_generated():
    actor = _actor()
    session = object()
    connection_status = AsyncMock(
        return_value={
            "connected": True,
            "expires_at": "2026-07-27T12:00:00+00:00",
        }
    )
    llm_fallback = AsyncMock(return_value="I cannot see whether Oura is connected.")

    with (
        patch.object(
            HealthTools,
            "get_oura_connection_status",
            new=connection_status,
            create=True,
        ),
        patch.object(
            MainOrchestrator,
            "_generate_general_response",
            new=llm_fallback,
        ),
    ):
        response = await MainOrchestrator.process_user_message(
            session=session,
            user_id=actor.user_id,
            household_id=actor.household_id,
            user_name="Denys",
            message_text="Подключилась ли моя Oura?",
        )

    connection_status.assert_awaited_once_with(
        session,
        user_id=actor.user_id,
    )
    llm_fallback.assert_not_awaited()
    assert "подключ" in response.lower()


@pytest.mark.asyncio
async def test_repeated_oura_command_reports_existing_connection_without_new_link():
    actor = _actor()
    message = _Message()
    connection_status = AsyncMock(return_value={"connected": True})

    with (
        patch("app.telegram.bot.unit_of_work", new=_fake_uow),
        patch("app.telegram.bot._resolve_actor", new=AsyncMock(return_value=actor)),
        patch.object(
            HealthTools,
            "get_oura_connection_status",
            new=connection_status,
            create=True,
        ),
        patch(
            "app.telegram.bot.OAuthStateManager.create_state",
            new=AsyncMock(return_value=("s" * 48, object())),
        ),
        patch(
            "app.telegram.bot.OuraClient.get_authorization_url",
            return_value="https://oura.example/authorize",
        ) as authorization_url,
    ):
        await telegram_module.cmd_oura_setup(message)

    connection_status.assert_awaited_once()
    authorization_url.assert_not_called()
    assert "подключ" in message.answers[0][0].lower()


@pytest.mark.asyncio
async def test_oura_reconnect_command_creates_fresh_authorization_for_connected_user():
    actor = _actor()
    message = _Message("/oura reconnect")

    with (
        patch("app.telegram.bot.unit_of_work", new=_fake_uow),
        patch("app.telegram.bot._resolve_actor", new=AsyncMock(return_value=actor)),
        patch.object(
            HealthTools,
            "get_oura_connection_status",
            new=AsyncMock(return_value={"connected": True}),
        ),
        patch(
            "app.telegram.bot.OAuthStateManager.create_state",
            new=AsyncMock(return_value=("s" * 48, object())),
        ) as create_state,
        patch(
            "app.telegram.bot.OuraClient.get_authorization_url",
            return_value="https://oura.example/authorize",
        ),
    ):
        await telegram_module.cmd_oura_setup(message)

    create_state.assert_awaited_once()
    assert "https://oura.example/authorize" in message.answers[0][0]


@pytest.mark.asyncio
async def test_oura_biometrics_query_uses_provider_data_not_llm_fallback():
    actor = _actor()
    daily_summary = AsyncMock(
        return_value={
            "date": "2026-07-26",
            "sleep_score": 82,
            "readiness_score": 77,
            "activity_score": 91,
        }
    )
    llm_fallback = AsyncMock(return_value="Generic health response")

    with (
        patch.object(
            HealthTools,
            "get_oura_daily_summary",
            new=daily_summary,
            create=True,
        ),
        patch.object(
            MainOrchestrator,
            "_generate_general_response",
            new=llm_fallback,
        ),
    ):
        response = await MainOrchestrator.process_user_message(
            session=object(),
            user_id=actor.user_id,
            household_id=actor.household_id,
            user_name="Denys",
            message_text="Как я сегодня спал по Oura?",
        )

    daily_summary.assert_awaited_once()
    llm_fallback.assert_not_awaited()
    assert "82" in response
    assert "77" in response
    assert "91" in response


@pytest.mark.asyncio
async def test_oura_connection_status_reads_only_current_users_record():
    user_id = uuid.uuid4()
    disconnected = await HealthTools.get_oura_connection_status(
        _session_returning(),
        user_id=user_id,
    )
    assert disconnected == {"connected": False, "expires_at": None}

    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    connected = await HealthTools.get_oura_connection_status(
        _session_returning(SimpleNamespace(expires_at=expires_at)),
        user_id=user_id,
    )
    assert connected == {
        "connected": True,
        "expires_at": expires_at.isoformat(),
    }


@pytest.mark.asyncio
async def test_valid_oura_access_token_is_decrypted_without_refresh():
    user_id = uuid.uuid4()
    cipher = _cipher()
    record = SimpleNamespace(
        access_token_encrypted=cipher.encrypt(
            "access",
            user_id=user_id,
            provider="oura",
            token_type="access_token",
        ),
        refresh_token_encrypted=cipher.encrypt(
            "refresh",
            user_id=user_id,
            provider="oura",
            token_type="refresh_token",
        ),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="daily",
    )

    token = await HealthTools.get_valid_oura_access_token(
        _session_returning(record),
        user_id=user_id,
        cipher=cipher,
    )

    assert token == "access"


@pytest.mark.asyncio
async def test_expired_oura_token_refresh_rotates_and_persists_both_tokens():
    user_id = uuid.uuid4()
    cipher = _cipher()
    record = SimpleNamespace(
        access_token_encrypted=cipher.encrypt(
            "old-access",
            user_id=user_id,
            provider="oura",
            token_type="access_token",
        ),
        refresh_token_encrypted=cipher.encrypt(
            "old-refresh",
            user_id=user_id,
            provider="oura",
            token_type="refresh_token",
        ),
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        scope="daily heartrate",
    )
    session = _session_returning(record)

    with patch.object(
        OuraClient,
        "refresh_access_token",
        new=AsyncMock(
            return_value={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            }
        ),
    ) as refresh:
        token = await HealthTools.get_valid_oura_access_token(
            session,
            user_id=user_id,
            cipher=cipher,
        )

    assert token == "new-access"
    refresh.assert_awaited_once_with("old-refresh")
    assert (
        cipher.decrypt(
            record.refresh_token_encrypted,
            user_id=user_id,
            provider="oura",
            token_type="refresh_token",
        )
        == "new-refresh"
    )
    assert record.scope == "daily heartrate"
    session.flush.assert_awaited()


@pytest.mark.asyncio
async def test_missing_oura_token_requires_connection():
    with pytest.raises(HealthIntegrationError, match="not connected"):
        await HealthTools.get_valid_oura_access_token(
            _session_returning(),
            user_id=uuid.uuid4(),
            cipher=_cipher(),
        )


@pytest.mark.asyncio
async def test_oura_refresh_uses_basic_auth_and_rotating_refresh_grant(monkeypatch):
    _configure_oura(monkeypatch)
    http = _HttpSession(
        _HttpResponse(
            payload={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            }
        )
    )
    monkeypatch.setattr(
        "app.integrations.oura.client.aiohttp.ClientSession",
        lambda **_kwargs: http,
    )

    result = await OuraClient.refresh_access_token("old-refresh")

    assert result["refresh_token"] == "new-refresh"
    method, _, kwargs = http.calls[0]
    assert method == "POST"
    assert kwargs["data"] == {
        "grant_type": "refresh_token",
        "refresh_token": "old-refresh",
    }
    assert kwargs["headers"]["Authorization"].startswith("Basic ")


@pytest.mark.asyncio
async def test_oura_daily_collection_uses_bearer_and_validated_date_range(monkeypatch):
    target_day = date(2026, 7, 26)
    http = _HttpSession(
        _HttpResponse(
            payload={
                "data": [
                    {
                        "day": target_day.isoformat(),
                        "score": 82,
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(
        "app.integrations.oura.client.aiohttp.ClientSession",
        lambda **_kwargs: http,
    )

    result = await OuraClient.get_daily_collection(
        "daily_sleep",
        access_token="access",
        start_date=target_day,
        end_date=target_day,
    )

    assert result["data"][0]["score"] == 82
    method, url, kwargs = http.calls[0]
    assert method == "GET"
    assert url.endswith("/daily_sleep")
    assert kwargs["headers"] == {"Authorization": "Bearer access"}
    assert kwargs["params"]["start_date"] == target_day.isoformat()

    with pytest.raises(ValueError):
        await OuraClient.get_daily_collection(
            "personal_info",
            access_token="access",
            start_date=target_day,
            end_date=target_day,
        )


@pytest.mark.asyncio
async def test_oura_daily_collection_rejects_provider_error_without_body(monkeypatch):
    target_day = date(2026, 7, 26)
    http = _HttpSession(
        _HttpResponse(
            status=401,
            payload={"error": "secret-provider-detail"},
        )
    )
    monkeypatch.setattr(
        "app.integrations.oura.client.aiohttp.ClientSession",
        lambda **_kwargs: http,
    )

    with pytest.raises(OuraOAuthError) as caught:
        await OuraClient.get_daily_collection(
            "daily_readiness",
            access_token="access",
            start_date=target_day,
            end_date=target_day,
        )

    assert caught.value.status_code == 401
    assert "secret-provider-detail" not in str(caught.value)


@pytest.mark.asyncio
async def test_oura_daily_summary_combines_detailed_provider_metrics():
    target_day = date(2026, 7, 26)
    collections = [
        {"data": [{"day": target_day.isoformat(), "score": 82}]},
        {
            "data": [
                {
                    "day": target_day.isoformat(),
                    "score": 77,
                    "temperature_deviation": 0.1,
                    "contributors": {"recovery_index": 78},
                }
            ]
        },
        {
            "data": [
                {
                    "day": target_day.isoformat(),
                    "score": 91,
                    "steps": 8431,
                    "active_calories": 521,
                    "total_calories": 2316,
                    "high_activity_time": 600,
                    "medium_activity_time": 2220,
                }
            ]
        },
        {
            "data": [
                {
                    "day": target_day.isoformat(),
                    "type": "rest",
                    "total_sleep_duration": 1800,
                },
                {
                    "day": target_day.isoformat(),
                    "type": "long_sleep",
                    "total_sleep_duration": 26280,
                    "deep_sleep_duration": 6120,
                    "rem_sleep_duration": 6900,
                    "awake_time": 1080,
                    "efficiency": 91,
                    "average_hrv": 61,
                    "lowest_heart_rate": 48,
                },
            ]
        },
        {
            "data": [
                {
                    "day": target_day.isoformat(),
                    "spo2_percentage": {"average": 97.4},
                    "breathing_disturbance_index": 1.2,
                }
            ]
        },
        {
            "data": [
                {
                    "day": target_day.isoformat(),
                    "day_summary": "normal",
                    "stress_high": 2520,
                    "recovery_high": 4080,
                }
            ]
        },
    ]
    with (
        patch.object(
            HealthTools,
            "get_valid_oura_access_token",
            new=AsyncMock(return_value="access"),
        ),
        patch.object(
            OuraClient,
            "get_daily_collection",
            new=AsyncMock(side_effect=collections),
        ) as get_collection,
    ):
        summary = await HealthTools.get_oura_daily_summary(
            object(),
            user_id=uuid.uuid4(),
            day=target_day,
        )

    assert summary["date"] == target_day.isoformat()
    assert summary["sleep_score"] == 82
    assert summary["readiness_score"] == 77
    assert summary["activity_score"] == 91
    assert summary["total_sleep_seconds"] == 26280
    assert summary["deep_sleep_seconds"] == 6120
    assert summary["rem_sleep_seconds"] == 6900
    assert summary["awake_seconds"] == 1080
    assert summary["sleep_efficiency"] == 91
    assert summary["average_hrv_ms"] == 61
    assert summary["lowest_heart_rate_bpm"] == 48
    assert summary["temperature_deviation_c"] == 0.1
    assert summary["steps"] == 8431
    assert summary["active_calories"] == 521
    assert summary["total_calories"] == 2316
    assert summary["spo2_average_percent"] == 97.4
    assert summary["stress_summary"] == "normal"
    assert summary["stress_high"] == 2520
    assert summary["recovery_high"] == 4080
    assert get_collection.await_count == 6


@pytest.mark.asyncio
async def test_oura_daily_summary_keeps_core_data_when_optional_endpoint_is_forbidden():
    target_day = date(2026, 7, 26)
    collections = [
        {"data": [{"day": target_day.isoformat(), "score": 73}]},
        {"data": [{"day": target_day.isoformat(), "score": 75}]},
        {"data": []},
        {"data": []},
        OuraOAuthError("Oura data request failed.", status_code=403),
        OuraOAuthError("Oura data request failed.", status_code=403),
    ]
    with (
        patch.object(
            HealthTools,
            "get_valid_oura_access_token",
            new=AsyncMock(return_value="access"),
        ),
        patch.object(
            OuraClient,
            "get_daily_collection",
            new=AsyncMock(side_effect=collections),
        ),
    ):
        summary = await HealthTools.get_oura_daily_summary(
            object(),
            user_id=uuid.uuid4(),
            day=target_day,
        )

    assert summary["sleep_score"] == 73
    assert summary["readiness_score"] == 75
    assert summary["activity_score"] is None
    assert summary["spo2_average_percent"] is None
    assert summary["stress_summary"] is None


def test_oura_summary_formatter_renders_rich_sections():
    response = MainOrchestrator._format_oura_summary(
        {
            "date": "2026-07-26",
            "sleep_score": 73,
            "total_sleep_seconds": 26280,
            "deep_sleep_seconds": 6120,
            "rem_sleep_seconds": 6900,
            "awake_seconds": 1080,
            "sleep_efficiency": 91,
            "readiness_score": 75,
            "average_hrv_ms": 61,
            "lowest_heart_rate_bpm": 48,
            "temperature_deviation_c": 0.1,
            "spo2_average_percent": 97.4,
            "activity_score": 80,
            "steps": 8431,
            "total_calories": 2316,
            "active_calories": 521,
            "high_activity_seconds": 600,
            "medium_activity_seconds": 2220,
            "stress_summary": "normal",
            "stress_high": 2520,
            "recovery_high": 4080,
            "analysis": "Сегодня лучше выбрать умеренную нагрузку.",
        }
    )

    assert "📅 26 июля 2026" in response
    assert "• Спал: 7ч 18м" in response
    assert "• Deep: 1ч 42м" in response
    assert "• REM: 1ч 55м" in response
    assert "• HRV: 61 ms" in response
    assert "• Min HR: 48 bpm" in response
    assert "• Температура: +0.1°C" in response
    assert "• Шаги: 8431" in response
    assert "• Средняя/высокая активность: 47м" in response
    assert "🧠 Стресс" in response
    assert "• Высокий стресс: 42м" in response
    assert "• Восстановление: 1ч 8м" in response
    assert "🤖 Анализ" in response
