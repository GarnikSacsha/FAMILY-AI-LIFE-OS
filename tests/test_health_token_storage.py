import base64
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from app.security.token_cipher import TokenCipher
from app.tools.health_tools import HealthTools


def make_session(existing_record=None):
    result = Mock()
    result.scalar_one_or_none.return_value = existing_record
    session = Mock()
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    session.add = Mock()
    return session


@pytest.mark.asyncio
async def test_oura_tokens_are_encrypted_and_expiry_is_calculated():
    session = make_session()
    user_id = uuid.uuid4()
    cipher = TokenCipher(base64.b64encode(b"k" * 32).decode("ascii"))
    before = datetime.now(timezone.utc)

    saved = await HealthTools.save_oura_tokens(
        session,
        user_id=user_id,
        tokens={
            "access_token": "plain-access-token",
            "refresh_token": "plain-refresh-token",
            "expires_in": 3600,
            "scope": "daily spo2",
        },
        cipher=cipher,
    )
    after = datetime.now(timezone.utc)

    assert saved is True
    session.add.assert_called_once()
    session.flush.assert_awaited_once()
    record = session.add.call_args.args[0]
    assert "plain-access-token" not in record.access_token_encrypted
    assert "plain-refresh-token" not in record.refresh_token_encrypted
    assert (
        cipher.decrypt(
            record.access_token_encrypted,
            user_id=user_id,
            provider="oura",
            token_type="access_token",
        )
        == "plain-access-token"
    )
    assert (
        cipher.decrypt(
            record.refresh_token_encrypted,
            user_id=user_id,
            provider="oura",
            token_type="refresh_token",
        )
        == "plain-refresh-token"
    )
    assert before + timedelta(seconds=3600) <= record.expires_at
    assert record.expires_at <= after + timedelta(seconds=3600)
    assert record.scope == "daily spo2"


@pytest.mark.asyncio
async def test_token_save_updates_existing_record_without_committing():
    existing = Mock()
    session = make_session(existing)
    user_id = uuid.uuid4()
    cipher = TokenCipher(base64.b64encode(b"k" * 32).decode("ascii"))

    await HealthTools.save_oura_tokens(
        session,
        user_id=user_id,
        tokens={
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": "60",
        },
        cipher=cipher,
    )

    session.add.assert_not_called()
    session.flush.assert_awaited_once()
    assert existing.access_token_encrypted.startswith("v1:")
    assert existing.refresh_token_encrypted.startswith("v1:")
    assert not hasattr(session, "commit") or session.commit.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tokens",
    [
        {"refresh_token": "refresh", "expires_in": 60},
        {"access_token": "access", "expires_in": 60},
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 0,
        },
    ],
)
async def test_invalid_token_response_is_rejected_before_database_access(tokens):
    session = make_session()
    cipher = TokenCipher(base64.b64encode(b"k" * 32).decode("ascii"))

    with pytest.raises(ValueError):
        await HealthTools.save_oura_tokens(
            session,
            user_id=uuid.uuid4(),
            tokens=tokens,
            cipher=cipher,
        )

    session.execute.assert_not_awaited()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_telegram_id_cannot_be_used_as_internal_user_id():
    session = make_session()
    cipher = TokenCipher(base64.b64encode(b"k" * 32).decode("ascii"))

    with pytest.raises(ValueError, match="internal user_id"):
        await HealthTools.save_oura_tokens(
            session,
            user_id=123456789,
            tokens={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 60,
            },
            cipher=cipher,
        )

    session.execute.assert_not_awaited()
