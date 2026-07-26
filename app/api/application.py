import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.oauth import router as oauth_router
from app.config.settings import settings
from app.infrastructure.database.session import engine
from app.telegram.bot import bot, start_bot

logger = logging.getLogger(__name__)


def create_application(*, start_telegram: bool = True) -> FastAPI:
    """Build the HTTP application and coordinate Telegram polling lifecycle."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        telegram_task: asyncio.Task[None] | None = None
        if start_telegram:
            telegram_task = asyncio.create_task(
                start_bot(),
                name="telegram-polling",
            )
            application.state.telegram_task = telegram_task

        try:
            yield
        finally:
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

    @application.get("/health", tags=["System"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_application()
