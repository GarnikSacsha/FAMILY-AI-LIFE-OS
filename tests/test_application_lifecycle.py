import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from aiogram.exceptions import TelegramConflictError
from aiogram.methods import GetUpdates

from app.api.application import create_application
from app.telegram.bot import (
    _track_polling_requests,
    bot,
    dp,
    polling_health,
    start_bot,
)


@pytest.mark.asyncio
async def test_health_fails_when_telegram_polling_stops() -> None:
    polling_stopped = asyncio.Event()

    async def stopped_bot() -> None:
        polling_stopped.set()

    with patch("app.api.application.start_bot", new=stopped_bot):
        application = create_application()

        async with application.router.lifespan_context(application):
            await asyncio.wait_for(polling_stopped.wait(), timeout=1)
            await asyncio.sleep(0)

            assert not application.state.telegram_task.done()
            assert polling_health.current_status() == "stopped"

            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.get("/health")
                live_response = await client.get("/live")

    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "telegram_polling": "stopped",
    }
    assert live_response.status_code == 200
    assert live_response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_application_starts_one_durable_reminder_worker_with_shared_bot() -> None:
    keep_polling = asyncio.Event()

    async def supervised_polling(_application: object) -> None:
        await keep_polling.wait()

    reminder_worker = AsyncMock()
    summary_worker = AsyncMock()
    with (
        patch(
            "app.api.application._supervise_telegram_polling",
            new=supervised_polling,
        ),
        patch(
            "app.api.application.run_reminder_worker",
            new=reminder_worker,
        ),
        patch(
            "app.api.application.run_conversation_summary_worker",
            new=summary_worker,
        ),
        patch(
            "app.api.application.GoogleSheetsClient.is_configured",
            return_value=False,
        ),
        patch.object(bot.session, "close", new=AsyncMock()),
    ):
        application = create_application()
        async with application.router.lifespan_context(application):
            await asyncio.sleep(0)

    reminder_worker.assert_awaited_once_with(bot)
    summary_worker.assert_awaited_once_with(bot)


@pytest.mark.asyncio
async def test_telegram_polling_restarts_after_a_terminal_failure() -> None:
    attempts = 0
    polling_restarted = asyncio.Event()
    keep_polling = asyncio.Event()

    async def flaky_bot() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("simulated terminal polling failure")
        polling_health.mark_success()
        polling_restarted.set()
        await keep_polling.wait()

    with (
        patch("app.api.application.start_bot", new=flaky_bot),
        patch("app.api.application.TELEGRAM_RETRY_INITIAL_SECONDS", new=0),
        patch(
            "app.api.application._database_is_ready",
            new=AsyncMock(return_value=True),
        ),
    ):
        application = create_application()

        async with application.router.lifespan_context(application):
            await asyncio.wait_for(polling_restarted.wait(), timeout=1)

            assert attempts == 2
            assert polling_health.current_status() == "running"

            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_bot_polling_does_not_own_process_signals_or_close_shared_session() -> None:
    identity = SimpleNamespace(username="family_test_bot", id=123)
    get_me = AsyncMock(return_value=identity)
    delete_webhook = AsyncMock()
    setup_commands = AsyncMock()
    start_polling = AsyncMock()

    with (
        patch.object(bot, "get_me", new=get_me),
        patch.object(bot, "delete_webhook", new=delete_webhook),
        patch("app.telegram.bot.setup_bot_commands", new=setup_commands),
        patch.object(dp, "start_polling", new=start_polling),
    ):
        await start_bot()

    get_me.assert_awaited_once_with()
    delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
    setup_commands.assert_awaited_once_with(bot)
    start_polling.assert_awaited_once_with(
        bot,
        handle_signals=False,
        close_bot_session=False,
    )


@pytest.mark.asyncio
async def test_get_updates_conflict_marks_polling_unhealthy() -> None:
    method = GetUpdates(timeout=10)

    async def conflicting_request(*_args: object) -> None:
        raise TelegramConflictError(
            method=method,
            message="simulated competing getUpdates consumer",
        )

    polling_health.reset(enabled=True)
    with pytest.raises(TelegramConflictError):
        await _track_polling_requests(conflicting_request, bot, method)

    assert polling_health.current_status() == "conflict"


@pytest.mark.asyncio
async def test_successful_get_updates_marks_polling_healthy() -> None:
    method = GetUpdates(timeout=10)

    async def successful_request(*_args: object) -> list[object]:
        return []

    polling_health.reset(enabled=True)
    result = await _track_polling_requests(successful_request, bot, method)

    assert result == []
    assert polling_health.current_status() == "running"


@pytest.mark.asyncio
async def test_health_fails_when_database_schema_is_unavailable() -> None:
    application = create_application(start_telegram=False)

    with patch(
        "app.api.application._database_is_ready",
        new=AsyncMock(return_value=False),
    ):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "database": "unavailable",
    }
