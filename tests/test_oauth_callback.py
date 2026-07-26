import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.api.oauth import oura_oauth_callback
from app.security.oauth import OAuthStateError


@pytest.mark.asyncio
async def test_valid_callback_saves_tokens_for_state_bound_user():
    session = AsyncMock()
    user_id = uuid.uuid4()
    tokens = {
        "access_token": "access",
        "refresh_token": "refresh",
        "expires_in": 3600,
    }

    with (
        patch(
            "app.api.oauth.OAuthStateManager.validate_and_consume_state",
            new=AsyncMock(return_value=user_id),
        ),
        patch(
            "app.api.oauth.OuraClient.exchange_code_for_tokens",
            new=AsyncMock(return_value=tokens),
        ),
        patch(
            "app.api.oauth.HealthTools.save_oura_tokens",
            new=AsyncMock(return_value=True),
        ) as save_tokens,
    ):
        response = await oura_oauth_callback(
            code="valid-code",
            state="valid-state",
            error=None,
            session=session,
        )

    assert response.status_code == 200
    save_tokens.assert_awaited_once_with(
        session,
        user_id=user_id,
        tokens=tokens,
    )


@pytest.mark.asyncio
async def test_invalid_state_is_rejected_before_token_exchange():
    session = AsyncMock()
    with (
        patch(
            "app.api.oauth.OAuthStateManager.validate_and_consume_state",
            new=AsyncMock(side_effect=OAuthStateError("invalid")),
        ),
        patch(
            "app.api.oauth.OuraClient.exchange_code_for_tokens",
            new=AsyncMock(),
        ) as exchange,
    ):
        response = await oura_oauth_callback(
            code="code",
            state="invalid",
            error=None,
            session=session,
        )

    assert response.status_code == 400
    exchange.assert_not_awaited()
    session.rollback.assert_awaited_once()
