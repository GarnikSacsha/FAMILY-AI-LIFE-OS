import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot

from app.infrastructure.database.session import AsyncSessionLocal
from app.tools.planner_tools import PlannerTools

logger = logging.getLogger(__name__)

REMINDER_POLL_INTERVAL_SECONDS = 5.0


async def deliver_due_reminders(bot_instance: Bot, *, now: datetime | None = None) -> int:
    """Claim and deliver all currently due reminders, returning the success count."""
    claimed_at = now or datetime.now(timezone.utc)
    async with AsyncSessionLocal.begin() as session:
        reminders = await PlannerTools.claim_due_reminders(session, now=claimed_at)

    delivered = 0
    for reminder in reminders:
        try:
            await bot_instance.send_message(
                chat_id=reminder["telegram_chat_id"],
                text=f"🔔 Напоминание\n\n{reminder['title']}",
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "Telegram reminder delivery failed (%s).",
                type(error).__name__,
            )
            async with AsyncSessionLocal.begin() as session:
                await PlannerTools.mark_reminder_failed(
                    session,
                    reminder_id=reminder["reminder_id"],
                    failed_at=datetime.now(timezone.utc),
                    error_code=type(error).__name__,
                )
            continue

        async with AsyncSessionLocal.begin() as session:
            await PlannerTools.mark_reminder_delivered(
                session,
                reminder_id=reminder["reminder_id"],
                delivered_at=datetime.now(timezone.utc),
            )
        delivered += 1
    return delivered


async def run_reminder_worker(
    bot_instance: Bot,
    *,
    poll_interval_seconds: float = REMINDER_POLL_INTERVAL_SECONDS,
) -> None:
    """Continuously deliver reminders while the application is running."""
    while True:
        await asyncio.sleep(poll_interval_seconds)
        try:
            await deliver_due_reminders(bot_instance)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "Reminder worker cycle failed (%s).",
                type(error).__name__,
            )
