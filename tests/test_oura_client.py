import base64
import urllib.parse

import aiohttp
import pytest
from pydantic import SecretStr

from app.config.settings import settings
from app.integrations.oura.client import OuraClient, OuraOAuthError


class FakeResponse:
    def __init__(self, *, status=200, payload=None):
        self.status = status
        self.payload = payload

    async def json(self):
        return self.payload


class ResponseContext:
    def __init__(self, response=None, enter_error=None):
        self.response = response
        self.enter_error = enter_error

    async def __aenter__(self):
        if self.enter_error:
            raise self.enter_error
        return self.response

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    created = []
    response_context = None

    def __init__(self, *, timeout):
        self.timeout = timeout
        self.post_args = None
        type(self).created.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def post(self, *args, **kwargs):
        self.post_args = (args, kwargs)
        return type(self).response_context


@pytest.fixture
def configured_oura(monkeypatch):
    monkeypatch.setattr(settings, "OURA_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "OURA_CLIENT_SECRET", SecretStr("client-secret"))
    monkeypatch.setattr(
        settings,
        "OURA_REDIRECT_URI",
        "http://localhost:8000/oauth/oura/callback",
    )
    monkeypatch.setattr(settings, "OURA_SCOPES", "daily heartrate spo2")
    FakeSession.created.clear()


def test_authorization_url_uses_state_and_supported_scopes(configured_oura):
    url = OuraClient.get_authorization_url("secure-random-state")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    assert query["state"] == ["secure-random-state"]
    assert query["scope"] == ["daily heartrate spo2"]
    assert "stress" not in query["scope"][0]
    assert query["redirect_uri"] == ["http://localhost:8000/oauth/oura/callback"]


@pytest.mark.asyncio
async def test_token_exchange_uses_timeout_basic_auth_and_validates_response(configured_oura, monkeypatch):
    FakeSession.response_context = ResponseContext(
        FakeResponse(
            payload={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": "3600",
            }
        )
    )
    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)

    result = await OuraClient.exchange_code_for_tokens("authorization-code")

    assert result["expires_in"] == 3600
    created = FakeSession.created[0]
    assert created.timeout.total == 10
    assert created.timeout.connect == 3
    _, kwargs = created.post_args
    expected_credentials = base64.b64encode(b"client-id:client-secret").decode("ascii")
    assert kwargs["headers"]["Authorization"] == f"Basic {expected_credentials}"
    assert "auth" not in kwargs
    assert "client_secret" not in kwargs["data"]
    assert kwargs["data"]["code"] == "authorization-code"


@pytest.mark.asyncio
async def test_provider_error_does_not_read_or_expose_response_body(configured_oura, monkeypatch):
    FakeSession.response_context = ResponseContext(FakeResponse(status=401))
    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)

    with pytest.raises(OuraOAuthError) as caught:
        await OuraClient.exchange_code_for_tokens("authorization-code")

    assert caught.value.status_code == 401
    assert "401" not in str(caught.value)
    assert "authorization-code" not in str(caught.value)


@pytest.mark.asyncio
async def test_timeout_is_mapped_to_safe_error(configured_oura, monkeypatch):
    FakeSession.response_context = ResponseContext(enter_error=TimeoutError())
    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)

    with pytest.raises(OuraOAuthError, match="unavailable") as caught:
        await OuraClient.exchange_code_for_tokens("authorization-code")

    assert "authorization-code" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"access_token": "access", "refresh_token": "refresh"},
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 0,
        },
    ],
)
async def test_invalid_success_payload_is_rejected(configured_oura, monkeypatch, payload):
    FakeSession.response_context = ResponseContext(FakeResponse(payload=payload))
    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)

    with pytest.raises(OuraOAuthError, match="invalid token response"):
        await OuraClient.exchange_code_for_tokens("authorization-code")
