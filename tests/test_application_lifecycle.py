import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.api.application import create_application
from app.telegram.bot import bot, dp, start_bot


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
            assert application.state.telegram_polling == "stopped"

            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "telegram_polling": "stopped",
    }


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
        polling_restarted.set()
        await keep_polling.wait()

    with (
        patch("app.api.application.start_bot", new=flaky_bot),
        patch("app.api.application.TELEGRAM_RETRY_INITIAL_SECONDS", new=0),
    ):
        application = create_application()

        async with application.router.lifespan_context(application):
            await asyncio.wait_for(polling_restarted.wait(), timeout=1)

            assert attempts == 2
            assert application.state.telegram_polling == "running"

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
    setup_commands = AsyncMock()
    start_polling = AsyncMock()

    with (
        patch("app.telegram.bot.setup_bot_commands", new=setup_commands),
        patch.object(dp, "start_polling", new=start_polling),
    ):
        await start_bot()

    setup_commands.assert_awaited_once_with(bot)
    start_polling.assert_awaited_once_with(
        bot,
        handle_signals=False,
        close_bot_session=False,
    )
