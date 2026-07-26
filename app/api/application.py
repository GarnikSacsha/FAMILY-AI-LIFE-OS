import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Response, status
from sqlalchemy import select

from app.api.oauth import router as oauth_router
from app.config.settings import settings
from app.domains.identity.models import User
from app.infrastructure.database.session import engine
from app.infrastructure.integrations.google_sheets_worker import (
    run_google_sheets_worker,
)
from app.integrations.google.sheets import GoogleSheetsClient
from app.telegram.bot import bot, polling_health, start_bot

logger = logging.getLogger(__name__)

TELEGRAM_RETRY_INITIAL_SECONDS = 1.0
TELEGRAM_RETRY_MAX_SECONDS = 30.0


async def _database_is_ready() -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(select(User.id).limit(1))
    except Exception as error:
        logger.error(
            "Database readiness failed (%s).",
            type(error).__name__,
        )
        return False
    return True


async def _supervise_telegram_polling(application: FastAPI) -> None:
    """Keep Telegram polling alive and expose failures through application state."""
    retry_delay = TELEGRAM_RETRY_INITIAL_SECONDS

    while True:
        try:
            await start_bot()
        except asyncio.CancelledError:
            polling_health.mark_stopped()
            raise
        except Exception as error:
            polling_health.mark_failure()
            logger.error(
                "Telegram polling failed (%s); retrying in %.1f seconds.",
                type(error).__name__,
                retry_delay,
            )
        else:
            polling_health.mark_stopped()
            logger.error(
                "Telegram polling stopped unexpectedly; retrying in %.1f seconds.",
                retry_delay,
            )

        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, TELEGRAM_RETRY_MAX_SECONDS)


def create_application(*, start_telegram: bool = True) -> FastAPI:
    """Build the HTTP application and coordinate Telegram polling lifecycle."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        telegram_task: asyncio.Task[None] | None = None
        sheets_task: asyncio.Task[None] | None = None
        polling_health.reset(enabled=start_telegram)
        if start_telegram:
            telegram_task = asyncio.create_task(
                _supervise_telegram_polling(application),
                name="telegram-polling-supervisor",
            )
            application.state.telegram_task = telegram_task
        if GoogleSheetsClient.is_configured():
            sheets_task = asyncio.create_task(
                run_google_sheets_worker(),
                name="google-sheets-sync",
            )
            application.state.google_sheets_task = sheets_task

        try:
            yield
        finally:
            if sheets_task is not None:
                sheets_task.cancel()
                with suppress(asyncio.CancelledError):
                    await sheets_task
            if telegram_task is not None:
                telegram_task.cancel()
                with suppress(asyncio.CancelledError):
                    await telegram_task
            await bot.session.close()
            await engine.dispose()
            logger.info("Application resources closed.")

    application = FastAPI(
        title=settings.APP_NAME,
        lifespan=lifespan,
    )
    application.include_router(oauth_router)

    @application.get("/live", tags=["System"])
    async def live() -> dict[str, str]:
        """Report HTTP process readiness without blocking singleton handover."""
        return {"status": "ok"}

    @application.get("/health", tags=["System"])
    async def health(response: Response) -> dict[str, str]:
        telegram_polling = polling_health.current_status()
        if start_telegram and telegram_polling != "running":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {
                "status": "error",
                "telegram_polling": telegram_polling,
            }
        if not await _database_is_ready():
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {
                "status": "error",
                "database": "unavailable",
            }
        return {"status": "ok"}

    return application


app = create_application()
