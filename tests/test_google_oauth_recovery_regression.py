"""Exercise token renewal through the real calendar command and OAuth client."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_google_workspace_paths import (
    _actor,
    _cipher,
    _configure_google_oauth,
    _HttpResponse,
    _HttpSession,
    _Message,
    _ScalarResult,
    _session_returning,
)

from app.telegram import bot


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["invalid_grant", "timeout", "server_error", "success"])
async def test_calendar_refresh_gives_actionable_recovery(monkeypatch, failure):
    _configure_google_oauth(monkeypatch)
    actor = _actor()
    cipher = _cipher()
    scope = "https://www.googleapis.com/auth/calendar.events"
    record = SimpleNamespace(
        access_token_encrypted=cipher.encrypt(
            "synthetic-old", user_id=actor.user_id, provider="google", token_type="access_token"
        ),
        refresh_token_encrypted=cipher.encrypt(
            "synthetic-refresh", user_id=actor.user_id, provider="google", token_type="refresh_token"
        ),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        scope=scope,
    )
    original_refresh = record.refresh_token_encrypted
    session = _session_returning(record)
    session.execute.side_effect = [_ScalarResult(scope), _ScalarResult(record), _ScalarResult(record)]

    @asynccontextmanager
    async def uow():
        yield session

    responses = {
        "invalid_grant": _HttpSession(_HttpResponse(400, {"error": "invalid_grant"})),
        "timeout": _HttpSession(error=TimeoutError()),
        "server_error": _HttpSession(_HttpResponse(503, {"error": "temporarily_unavailable"})),
        "success": _HttpSession(_HttpResponse(payload={"access_token": "synthetic-new", "expires_in": 3600})),
    }
    http = responses[failure]
    monkeypatch.setattr(bot, "unit_of_work", uow)
    monkeypatch.setattr(bot, "_resolve_actor", AsyncMock(return_value=actor))
    monkeypatch.setattr("app.tools.google_tools.get_token_cipher", lambda: cipher)
    monkeypatch.setattr("app.integrations.google.oauth.aiohttp.ClientSession", lambda **kwargs: http)
    calendar_read = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("app.tools.google_tools.GoogleWorkspaceTools._get_json", calendar_read)
    message = _Message("/calendar")

    await bot.cmd_calendar(message)

    assert len(http.calls) == 1
    assert http.calls[0][2]["data"]["grant_type"] == "refresh_token"
    assert record.refresh_token_encrypted == original_refresh
    assert len(message.answers) == 1
    answer = message.answers[0][0]
    if failure == "success":
        calendar_read.assert_awaited_once()
        assert calendar_read.await_args.kwargs["access_token"] == "synthetic-new"
        assert record.expires_at > datetime.now(timezone.utc)
        assert "Ближайших событий не найдено" in answer
    else:
        calendar_read.assert_not_awaited()
        if failure == "invalid_grant":
            assert "/google reconnect" in answer, answer
        else:
            assert "/google" not in answer, answer
            assert "позже" in answer.lower() or "временно" in answer.lower(), answer
