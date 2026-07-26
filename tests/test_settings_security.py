import base64

import pytest
from pydantic import ValidationError

from app.config.settings import Settings

VALID_KEY = base64.b64encode(b"0" * 32).decode("ascii")


def production_settings(**overrides):
    values = {
        "ENVIRONMENT": "production",
        "SECRET_KEY": "a-real-production-secret-with-sufficient-entropy",
        "TELEGRAM_BOT_TOKEN": "987654321:production-token-value",
        "DENYS_TELEGRAM_ID": 1001,
        "OLEKSANDRA_TELEGRAM_ID": 1002,
        "OURA_CLIENT_ID": "oura-client",
        "OURA_CLIENT_SECRET": "oura-client-secret",
        "OURA_REDIRECT_URI": "https://family.example.com/oauth/oura/callback",
        "TOKEN_ENCRYPTION_KEY": VALID_KEY,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_development_settings_are_usable_without_production_secrets(monkeypatch):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    configured = Settings(ENVIRONMENT="development", _env_file=None)

    assert configured.TOKEN_ENCRYPTION_KEY is None
    assert configured.HTTP_PORT == 8000


def test_railway_port_alias_is_supported(monkeypatch):
    monkeypatch.delenv("HTTP_PORT", raising=False)
    monkeypatch.setenv("PORT", "54321")

    configured = Settings(ENVIRONMENT="test", _env_file=None)

    assert configured.HTTP_PORT == 54321


def test_production_requires_token_encryption_key():
    with pytest.raises(ValidationError, match="TOKEN_ENCRYPTION_KEY"):
        production_settings(TOKEN_ENCRYPTION_KEY=None)


@pytest.mark.parametrize(
    "invalid_key",
    [
        "not valid base64!",
        base64.b64encode(b"short").decode("ascii"),
        base64.b64encode(b"x" * 33).decode("ascii"),
    ],
)
def test_token_encryption_key_must_be_valid_base64_for_32_bytes(invalid_key):
    with pytest.raises(ValidationError, match="TOKEN_ENCRYPTION_KEY"):
        Settings(
            ENVIRONMENT="test",
            TOKEN_ENCRYPTION_KEY=invalid_key,
            _env_file=None,
        )


def test_production_requires_https_callback_on_registered_route():
    with pytest.raises(ValidationError, match="OURA_REDIRECT_URI"):
        production_settings(OURA_REDIRECT_URI="http://localhost:8000/oauth/oura/callback")

    with pytest.raises(ValidationError, match="OURA_REDIRECT_URI"):
        production_settings(OURA_REDIRECT_URI="https://family.example.com/oura/callback")


def test_secret_values_are_redacted():
    configured = production_settings()

    assert str(configured.TOKEN_ENCRYPTION_KEY) == "**********"
    assert str(configured.OURA_CLIENT_SECRET) == "**********"


def test_unsupported_oura_scope_is_rejected():
    with pytest.raises(ValidationError, match="OURA_SCOPES"):
        Settings(
            ENVIRONMENT="test",
            OURA_SCOPES="daily stress",
            _env_file=None,
        )
