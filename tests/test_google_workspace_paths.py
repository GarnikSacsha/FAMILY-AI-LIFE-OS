import asyncio
import base64
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import aiohttp
import pytest
from pydantic import SecretStr

from app.api.oauth import google_oauth_callback
from app.config.settings import settings
from app.domains.identity.service import ActorContext, PermissionDeniedError
from app.infrastructure.integrations import google_sheets_worker as worker_module
from app.integrations.google.oauth import GoogleOAuthClient, GoogleOAuthError
from app.integrations.google.sheets import GoogleSheetsClient, GoogleSheetsError
from app.security.oauth import OAuthStateError
from app.security.token_cipher import TokenCipher
from app.telegram import bot as telegram_module
from app.tools.google_tools import GoogleWorkspaceError, GoogleWorkspaceTools


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False


class _HttpResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = payload if payload is not None else {}

    async def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class _HttpSession:
    def __init__(self, response=None, error=None):
        self.response = response or _HttpResponse()
        self.error = error
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def _call(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error:
            raise self.error
        return _AsyncContext(self.response)

    def post(self, url, **kwargs):
        return self._call("POST", url, **kwargs)

    def get(self, url, **kwargs):
        return self._call("GET", url, **kwargs)

    def request(self, method, url, **kwargs):
        return self._call(method, url, **kwargs)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


def _session_returning(record=None):
    session = MagicMock()
    session.execute = AsyncMock(return_value=_ScalarResult(record))
    session.flush = AsyncMock()
    session.add = Mock()
    return session


def _cipher() -> TokenCipher:
    return TokenCipher(base64.b64encode(b"w" * 32).decode("ascii"))


def _actor(chat_type="private") -> ActorContext:
    return ActorContext(
        user_id=uuid.uuid4(),
        telegram_id=123456789,
        household_id=uuid.uuid4(),
        chat_id=123456789,
        chat_type=chat_type,
    )


class _Message:
    def __init__(self, text="/google"):
        self.from_user = SimpleNamespace(id=123456789, first_name="Denys")
        self.chat = SimpleNamespace(id=123456789, type="private")
        self.text = text
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


@asynccontextmanager
async def _fake_uow():
    yield object()


def _configure_google_oauth(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", SecretStr("client-secret"))
    monkeypatch.setattr(
        settings,
        "GOOGLE_OAUTH_REDIRECT_URI",
        "https://family.example.com/oauth/google/callback",
    )


def test_google_oauth_configuration_and_input_guards(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", None)
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", None)
    assert GoogleOAuthClient.is_configured() is False
    with pytest.raises(GoogleOAuthError):
        GoogleOAuthClient._client_credentials()

    _configure_google_oauth(monkeypatch)
    assert GoogleOAuthClient.is_configured() is True
    assert GoogleOAuthClient._client_credentials() == ("client-id", "client-secret")
    with pytest.raises(GoogleOAuthError):
        GoogleOAuthClient.get_authorization_url(state="short")


@pytest.mark.asyncio
async def test_google_oauth_token_exchange_and_refresh(monkeypatch):
    _configure_google_oauth(monkeypatch)
    session = _HttpSession(
        _HttpResponse(
            payload={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
            }
        )
    )
    monkeypatch.setattr("app.integrations.google.oauth.aiohttp.ClientSession", lambda **_kwargs: session)

    exchanged = await GoogleOAuthClient.exchange_code_for_tokens("code")
    refreshed = await GoogleOAuthClient.refresh_access_token("refresh")

    assert exchanged["access_token"] == "access"
    assert refreshed["expires_in"] == 3600
    assert session.calls[0][2]["data"]["grant_type"] == "authorization_code"
    assert session.calls[1][2]["data"]["grant_type"] == "refresh_token"
    with pytest.raises(GoogleOAuthError):
        await GoogleOAuthClient.exchange_code_for_tokens("")
    with pytest.raises(GoogleOAuthError):
        await GoogleOAuthClient.refresh_access_token("")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error"),
    [
        (_HttpResponse(status=401), None),
        (_HttpResponse(payload={"expires_in": 3600}), None),
        (_HttpResponse(payload={"access_token": "a", "expires_in": 0}), None),
        (None, aiohttp.ClientConnectionError()),
    ],
)
async def test_google_oauth_token_failures_are_safe(monkeypatch, response, error):
    _configure_google_oauth(monkeypatch)
    session = _HttpSession(response=response, error=error)
    monkeypatch.setattr("app.integrations.google.oauth.aiohttp.ClientSession", lambda **_kwargs: session)

    with pytest.raises(GoogleOAuthError, match="Google"):
        await GoogleOAuthClient.exchange_code_for_tokens("code")


@pytest.mark.asyncio
async def test_google_callback_cancel_missing_state_and_safe_failures():
    cancelled = await google_oauth_callback(
        session=object(),
        code=None,
        state=None,
        error="access_denied",
    )
    assert cancelled.status_code == 400

    with pytest.raises(Exception) as missing:
        await google_oauth_callback(
            session=object(),
            code=None,
            state="state",
            error=None,
        )
    assert getattr(missing.value, "status_code", None) == 400

    session = AsyncMock()
    with patch(
        "app.api.oauth.OAuthStateManager.validate_and_consume_state",
        new=AsyncMock(side_effect=OAuthStateError("expired")),
    ):
        invalid = await google_oauth_callback(
            session=session,
            code="code",
            state="state",
            error=None,
        )
    assert invalid.status_code == 400
    session.rollback.assert_awaited()

    with (
        patch(
            "app.api.oauth.OAuthStateManager.validate_and_consume_state",
            new=AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            "app.api.oauth.GoogleOAuthClient.exchange_code_for_tokens",
            new=AsyncMock(side_effect=RuntimeError("provider")),
        ),
    ):
        failed = await google_oauth_callback(
            session=session,
            code="code",
            state="state",
            error=None,
        )
    assert failed.status_code == 500


def test_sheets_credentials_configuration_and_validation(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_SPREADSHEET_ID", None)
    monkeypatch.setattr(settings, "GOOGLE_CREDENTIALS_JSON", None)
    monkeypatch.setattr(settings, "GOOGLE_CREDENTIALS_JSON_PATH", None)
    assert GoogleSheetsClient.is_configured() is False
    with pytest.raises(GoogleSheetsError):
        GoogleSheetsClient._credentials_info()

    monkeypatch.setattr(settings, "GOOGLE_SHEETS_SPREADSHEET_ID", "sheet")
    monkeypatch.setattr(settings, "GOOGLE_CREDENTIALS_JSON", SecretStr("{"))
    assert GoogleSheetsClient.is_configured() is True
    with pytest.raises(GoogleSheetsError):
        GoogleSheetsClient._credentials_info()

    monkeypatch.setattr(settings, "GOOGLE_CREDENTIALS_JSON", SecretStr('{"type":"authorized_user"}'))
    with pytest.raises(GoogleSheetsError):
        GoogleSheetsClient._credentials_info()

    path = tmp_path / "service.json"
    path.write_text(json.dumps({"type": "service_account"}), encoding="utf-8")
    monkeypatch.setattr(settings, "GOOGLE_CREDENTIALS_JSON", None)
    monkeypatch.setattr(settings, "GOOGLE_CREDENTIALS_JSON_PATH", str(path))
    assert GoogleSheetsClient._credentials_info()["type"] == "service_account"


@pytest.mark.asyncio
async def test_sheets_access_token_success_and_safe_failure(monkeypatch):
    credentials = SimpleNamespace(token=None)

    async def refresh_in_thread(function, request):
        function(request)

    def refresh(_request):
        credentials.token = "sheet-token"

    credentials.refresh = refresh
    monkeypatch.setattr(
        "app.integrations.google.sheets.GoogleSheetsClient._credentials_info",
        lambda: {"type": "service_account"},
    )
    monkeypatch.setattr(
        "app.integrations.google.sheets.service_account.Credentials.from_service_account_info",
        lambda *_args, **_kwargs: credentials,
    )
    monkeypatch.setattr("app.integrations.google.sheets.asyncio.to_thread", refresh_in_thread)
    assert await GoogleSheetsClient._access_token() == "sheet-token"

    monkeypatch.setattr(
        "app.integrations.google.sheets.service_account.Credentials.from_service_account_info",
        Mock(side_effect=ValueError("invalid")),
    )
    with pytest.raises(GoogleSheetsError):
        await GoogleSheetsClient._access_token()


@pytest.mark.asyncio
async def test_sheets_request_success_and_failures(monkeypatch):
    success = _HttpSession(_HttpResponse(payload={"values": []}))
    monkeypatch.setattr("app.integrations.google.sheets.aiohttp.ClientSession", lambda **_kwargs: success)
    assert await GoogleSheetsClient._request("GET", "https://sheets.example", access_token="token") == {"values": []}

    rejected = _HttpSession(_HttpResponse(status=403))
    monkeypatch.setattr("app.integrations.google.sheets.aiohttp.ClientSession", lambda **_kwargs: rejected)
    with pytest.raises(GoogleSheetsError):
        await GoogleSheetsClient._request("GET", "https://sheets.example", access_token="token")

    broken = _HttpSession(error=aiohttp.ClientConnectionError())
    monkeypatch.setattr("app.integrations.google.sheets.aiohttp.ClientSession", lambda **_kwargs: broken)
    with pytest.raises(GoogleSheetsError):
        await GoogleSheetsClient._request("GET", "https://sheets.example", access_token="token")


@pytest.mark.asyncio
async def test_sheets_append_guards_missing_configuration(monkeypatch):
    with pytest.raises(ValueError):
        await GoogleSheetsClient.append_transaction(["too", "short"])

    monkeypatch.setattr(settings, "GOOGLE_SHEETS_SPREADSHEET_ID", None)
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_RANGE", "A:I")
    with pytest.raises(GoogleSheetsError):
        await GoogleSheetsClient.append_transaction([""] * 9)


@pytest.mark.asyncio
async def test_google_tokens_update_and_access_refresh_paths():
    user_id = uuid.uuid4()
    cipher = _cipher()
    now = datetime.now(timezone.utc)
    record = SimpleNamespace(
        access_token_encrypted=cipher.encrypt(
            "old-access",
            user_id=user_id,
            provider="google",
            token_type="access_token",
        ),
        refresh_token_encrypted=cipher.encrypt(
            "refresh",
            user_id=user_id,
            provider="google",
            token_type="refresh_token",
        ),
        expires_at=now + timedelta(hours=1),
        scope="old",
    )
    session = _session_returning(record)
    access = await GoogleWorkspaceTools.get_valid_access_token(
        session,
        user_id=user_id,
        cipher=cipher,
    )
    assert access == "old-access"

    record.expires_at = now - timedelta(seconds=1)
    with (
        patch(
            "app.tools.google_tools.GoogleOAuthClient.refresh_access_token",
            new=AsyncMock(
                return_value={
                    "access_token": "new-access",
                    "expires_in": 3600,
                    "scope": "new",
                }
            ),
        ),
        patch.object(
            GoogleWorkspaceTools,
            "save_google_tokens",
            new=AsyncMock(),
        ) as save,
    ):
        access = await GoogleWorkspaceTools.get_valid_access_token(
            session,
            user_id=user_id,
            cipher=cipher,
        )
    assert access == "new-access"
    save.assert_awaited_once()

    await GoogleWorkspaceTools.save_google_tokens(
        session,
        user_id=user_id,
        tokens={
            "access_token": "updated",
            "refresh_token": "updated-refresh",
            "expires_in": 1800,
            "scope": "updated",
        },
        cipher=cipher,
    )
    assert record.scope == "updated"


@pytest.mark.asyncio
async def test_google_token_missing_and_refresh_failure_are_safe():
    user_id = uuid.uuid4()
    cipher = _cipher()
    with pytest.raises(GoogleWorkspaceError):
        await GoogleWorkspaceTools.get_valid_access_token(
            _session_returning(),
            user_id=user_id,
            cipher=cipher,
        )

    record = SimpleNamespace(
        access_token_encrypted="unused",
        refresh_token_encrypted=None,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    with pytest.raises(GoogleWorkspaceError):
        await GoogleWorkspaceTools.get_valid_access_token(
            _session_returning(record),
            user_id=user_id,
            cipher=cipher,
        )

    record.refresh_token_encrypted = cipher.encrypt(
        "refresh",
        user_id=user_id,
        provider="google",
        token_type="refresh_token",
    )
    with (
        patch(
            "app.tools.google_tools.GoogleOAuthClient.refresh_access_token",
            new=AsyncMock(side_effect=GoogleOAuthError("provider")),
        ),
        pytest.raises(GoogleWorkspaceError),
    ):
        await GoogleWorkspaceTools.get_valid_access_token(
            _session_returning(record),
            user_id=user_id,
            cipher=cipher,
        )


@pytest.mark.asyncio
async def test_google_workspace_json_mail_and_calendar_paths(monkeypatch):
    http = _HttpSession(_HttpResponse(payload={"ok": True}))
    monkeypatch.setattr("app.tools.google_tools.aiohttp.ClientSession", lambda **_kwargs: http)
    assert await GoogleWorkspaceTools._get_json("https://google.example", access_token="token") == {"ok": True}

    with patch.object(
        GoogleWorkspaceTools,
        "get_valid_access_token",
        new=AsyncMock(return_value="token"),
    ):
        no_mail = AsyncMock(return_value={})
        with patch.object(GoogleWorkspaceTools, "_get_json", new=no_mail):
            assert await GoogleWorkspaceTools.list_recent_mail(object(), user_id=uuid.uuid4()) == []

        responses = [
            {"messages": [{"id": "m1"}]},
            {
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Hello"},
                        {"name": "From", "value": "Family"},
                    ]
                },
                "snippet": "Preview",
            },
        ]
        with patch.object(GoogleWorkspaceTools, "_get_json", new=AsyncMock(side_effect=responses)):
            messages = await GoogleWorkspaceTools.list_recent_mail(object(), user_id=uuid.uuid4())
        assert messages[0]["subject"] == "Hello"
        assert messages[0]["from"] == "Family"

        no_events = AsyncMock(return_value={})
        with (
            patch.object(GoogleWorkspaceTools, "_require_calendar_scope", new=AsyncMock()),
            patch.object(GoogleWorkspaceTools, "_get_json", new=no_events),
        ):
            assert await GoogleWorkspaceTools.list_upcoming_events(object(), user_id=uuid.uuid4()) == []

        with (
            patch.object(GoogleWorkspaceTools, "_require_calendar_scope", new=AsyncMock()),
            patch.object(
                GoogleWorkspaceTools,
                "_get_json",
                new=AsyncMock(
                    return_value={
                        "items": [
                            {
                                "id": "e1",
                                "summary": "Dinner",
                                "start": {"dateTime": "2026-07-27T18:00:00+03:00"},
                            }
                        ]
                    }
                ),
            ),
        ):
            events = await GoogleWorkspaceTools.list_upcoming_events(object(), user_id=uuid.uuid4())
        assert events[0]["summary"] == "Dinner"


@pytest.mark.asyncio
async def test_list_upcoming_events_deduplicates_recurring_series_occurrences():
    recurring_payload = {
        "items": [
            {
                "id": "arcade-20260807",
                "recurringEventId": "arcade-series",
                "summary": "Arcade Daily Trading",
                "start": {"dateTime": "2026-08-07T14:00:00+03:00"},
            },
            {
                "id": "arcade-20260814",
                "recurringEventId": "arcade-series",
                "summary": "Arcade Daily Trading",
                "start": {"dateTime": "2026-08-14T14:00:00+03:00"},
            },
            {
                "id": "arcade-20260821",
                "recurringEventId": "arcade-series",
                "summary": "Arcade Daily Trading",
                "start": {"dateTime": "2026-08-21T14:00:00+03:00"},
            },
        ]
    }
    with (
        patch.object(GoogleWorkspaceTools, "_require_calendar_scope", new=AsyncMock()),
        patch.object(GoogleWorkspaceTools, "get_valid_access_token", new=AsyncMock(return_value="token")),
        patch.object(GoogleWorkspaceTools, "_get_json", new=AsyncMock(return_value=recurring_payload)),
    ):
        events = await GoogleWorkspaceTools.list_upcoming_events(object(), user_id=uuid.uuid4(), limit=10)

    assert len(events) == 1
    assert events[0]["id"] == "arcade-series"
    assert events[0]["summary"] == "Arcade Daily Trading"
    assert events[0]["start"] == "2026-08-07T14:00:00+03:00"


@pytest.mark.asyncio
async def test_worker_claim_mark_and_loop_paths(monkeypatch):
    transaction = SimpleNamespace(
        id=uuid.uuid4(),
        occurred_at=datetime.now(timezone.utc),
        merchant="Market",
        category="Groceries",
        amount="19.00",
        currency="UAH",
        direction="expense",
        source="manual",
        owner_type="household",
        sheets_sync_status="pending",
        sheets_sync_attempts=0,
        sheets_sync_error="old",
    )
    session = MagicMock()
    session.execute = AsyncMock(return_value=_ScalarResult(transaction))

    @asynccontextmanager
    async def begin():
        yield session

    monkeypatch.setattr(worker_module, "AsyncSessionLocal", SimpleNamespace(begin=begin))
    item = await worker_module._claim_next_transaction()
    assert item is not None
    assert item.id == transaction.id
    assert transaction.sheets_sync_status == "syncing"
    assert transaction.sheets_sync_attempts == 1

    await worker_module._mark_synced(transaction.id, "Расходы!A2:I2")
    await worker_module._mark_failed(transaction.id, RuntimeError("secret text"))
    assert session.execute.await_count == 3

    monkeypatch.setattr(GoogleSheetsClient, "is_configured", classmethod(lambda cls: True))
    monkeypatch.setattr(
        worker_module,
        "_claim_next_transaction",
        AsyncMock(side_effect=[item, asyncio.CancelledError()]),
    )
    append = AsyncMock(return_value="Расходы!A2:I2")
    mark_synced = AsyncMock()
    monkeypatch.setattr(GoogleSheetsClient, "append_transaction", append)
    monkeypatch.setattr(worker_module, "_mark_synced", mark_synced)
    with pytest.raises(asyncio.CancelledError):
        await worker_module.run_google_sheets_worker()
    append.assert_awaited_once()
    mark_synced.assert_awaited_once_with(item.id, "Расходы!A2:I2")

    monkeypatch.setattr(
        worker_module,
        "_claim_next_transaction",
        AsyncMock(side_effect=[item, asyncio.CancelledError()]),
    )
    monkeypatch.setattr(
        GoogleSheetsClient,
        "append_transaction",
        AsyncMock(side_effect=RuntimeError("provider")),
    )
    mark_failed = AsyncMock()
    monkeypatch.setattr(worker_module, "_mark_failed", mark_failed)
    with pytest.raises(asyncio.CancelledError):
        await worker_module.run_google_sheets_worker()
    mark_failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_google_telegram_commands_success_empty_and_access_errors():
    actor = _actor()
    google_message = _Message("/google reconnect")
    with (
        patch("app.telegram.bot.unit_of_work", new=_fake_uow),
        patch("app.telegram.bot._resolve_actor", new=AsyncMock(return_value=actor)),
        patch(
            "app.telegram.bot.OAuthStateManager.create_state",
            new=AsyncMock(return_value=("s" * 48, object())),
        ),
        patch(
            "app.telegram.bot.GoogleOAuthClient.get_authorization_url",
            return_value="https://accounts.google.example/auth",
        ),
    ):
        await telegram_module.cmd_google_setup(google_message)
    assert "https://accounts.google.example/auth" in google_message.answers[0][0]

    denied = _Message()
    with (
        patch("app.telegram.bot.unit_of_work", new=_fake_uow),
        patch(
            "app.telegram.bot._resolve_actor",
            new=AsyncMock(side_effect=PermissionDeniedError("denied")),
        ),
    ):
        await telegram_module.cmd_google_setup(denied)
    assert denied.answers

    mail = _Message("/mail")
    with (
        patch("app.telegram.bot.unit_of_work", new=_fake_uow),
        patch("app.telegram.bot._resolve_actor", new=AsyncMock(return_value=actor)),
        patch(
            "app.telegram.bot.GoogleWorkspaceTools.list_important_mail",
            new=AsyncMock(
                return_value=[
                    {
                        "subject": "Hello",
                        "from": "Family",
                        "category": "Личное",
                        "reason": "Письмо от живого отправителя без рекламных признаков",
                    }
                ]
            ),
        ),
    ):
        await telegram_module.cmd_mail(mail)
    assert "Hello" in mail.answers[0][0]

    empty_mail = _Message("/mail")
    with (
        patch("app.telegram.bot.unit_of_work", new=_fake_uow),
        patch("app.telegram.bot._resolve_actor", new=AsyncMock(return_value=actor)),
        patch(
            "app.telegram.bot.GoogleWorkspaceTools.list_important_mail",
            new=AsyncMock(return_value=[]),
        ),
    ):
        await telegram_module.cmd_mail(empty_mail)
    assert "ничего" in empty_mail.answers[0][0]

    calendar = _Message("/calendar")
    with (
        patch("app.telegram.bot.unit_of_work", new=_fake_uow),
        patch("app.telegram.bot._resolve_actor", new=AsyncMock(return_value=actor)),
        patch(
            "app.telegram.bot.GoogleWorkspaceTools.list_upcoming_events",
            new=AsyncMock(return_value=[{"summary": "Dinner", "start": "2026-07-27T18:00:00+03:00"}]),
        ),
    ):
        await telegram_module.cmd_calendar(calendar)
    assert "Dinner" in calendar.answers[0][0]

    disconnected = _Message("/calendar")
    with (
        patch("app.telegram.bot.unit_of_work", new=_fake_uow),
        patch("app.telegram.bot._resolve_actor", new=AsyncMock(return_value=actor)),
        patch(
            "app.telegram.bot.GoogleWorkspaceTools.list_upcoming_events",
            new=AsyncMock(side_effect=GoogleWorkspaceError("not connected")),
        ),
    ):
        await telegram_module.cmd_calendar(disconnected)
    assert "/google" in disconnected.answers[0][0]


@pytest.mark.asyncio
async def test_google_command_reports_existing_connection_instead_of_restarting_oauth():
    actor = _actor()
    connected_token = SimpleNamespace(
        provider="google",
        access_token_encrypted="encrypted-access",
        refresh_token_encrypted="encrypted-refresh",
    )
    session = _session_returning(connected_token)

    @asynccontextmanager
    async def connected_uow():
        yield session

    message = _Message("/google")
    with (
        patch("app.telegram.bot.unit_of_work", new=connected_uow),
        patch("app.telegram.bot._resolve_actor", new=AsyncMock(return_value=actor)),
        patch(
            "app.telegram.bot.OAuthStateManager.create_state",
            new=AsyncMock(return_value=("s" * 48, object())),
        ) as create_state,
        patch(
            "app.telegram.bot.GoogleOAuthClient.get_authorization_url",
            return_value="https://accounts.google.example/auth",
        ),
    ):
        await telegram_module.cmd_google_setup(message)

    assert "уже подключён" in message.answers[0][0]
    create_state.assert_not_awaited()
