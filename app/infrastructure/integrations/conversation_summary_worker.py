import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot

from app.agents.memory.agent import SharedMemoryAgent, format_summary
from app.config.settings import settings
from app.domains.memory.models import SharedConversationSummary
from app.infrastructure.database.session import AsyncSessionLocal
from app.tools.finance_tools import FinanceTools
from app.tools.memory_tools import SharedMemoryTools

logger = logging.getLogger(__name__)


async def retry_due_shared_context_messages(
    *,
    now: datetime | None = None,
) -> int:
    """Persist Telegram-delivered assistant replies that missed shared context."""
    if not settings.SHARED_CHAT_MEMORY_ENABLED:
        return 0
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    async with AsyncSessionLocal.begin() as session:
        retries = await SharedMemoryTools.claim_due_message_retries(session, now=current)

    delivered = 0
    for retry in retries:
        try:
            async with AsyncSessionLocal.begin() as session:
                await SharedMemoryTools.record_message(
                    session,
                    household_id=retry.household_id,
                    author_user_id=None,
                    telegram_chat_id=retry.telegram_chat_id,
                    telegram_message_id=retry.telegram_message_id,
                    role="assistant",
                    author_name=retry.author_name,
                    message_type=retry.message_type,
                    content=retry.content,
                )
                await SharedMemoryTools.mark_message_retry_delivered(
                    session,
                    retry_id=retry.id,
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "Shared-context retry failed (%s).",
                type(error).__name__,
            )
            async with AsyncSessionLocal.begin() as session:
                await SharedMemoryTools.reschedule_message_retry(
                    session,
                    retry_id=retry.id,
                    error_code=type(error).__name__,
                    now=current,
                )
        else:
            delivered += 1
    return delivered


_CATEGORY_LABELS = {
    "Groceries": "Продукты",
    "Restaurants": "Кафе и рестораны",
    "Transport": "Транспорт",
    "Entertainment": "Развлечения",
    "Health": "Здоровье",
    "Utilities": "Коммунальные услуги",
    "Subscriptions": "Подписки",
    "Shopping": "Покупки",
    "Other": "Другое",
}


async def _deliver_summary(bot_instance: Bot, summary: SharedConversationSummary) -> bool:
    try:
        await bot_instance.send_message(
            chat_id=summary.telegram_chat_id,
            text=summary.summary_text,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.warning(
            "Shared-chat summary delivery failed (%s).",
            type(error).__name__,
        )
        async with AsyncSessionLocal.begin() as session:
            await SharedMemoryTools.mark_summary_delivery(
                session,
                summary_id=summary.id,
                error_code=type(error).__name__,
            )
        return False

    async with AsyncSessionLocal.begin() as session:
        await SharedMemoryTools.mark_summary_delivery(
            session,
            summary_id=summary.id,
            delivered_at=datetime.now(timezone.utc),
        )
    return True


async def _remember_summary_items(
    session,
    *,
    household_id,
    source_message_id,
    structured_data: dict[str, list[str]],
) -> None:
    memory_kind = {
        "decisions": "decision",
        "actions": "action",
        "money": "money",
        "open_questions": "open_question",
        "facts": "fact",
        "suggestions": "suggestion",
    }
    for section, kind in memory_kind.items():
        for content in structured_data.get(section, []):
            await SharedMemoryTools.remember(
                session,
                household_id=household_id,
                source_message_id=source_message_id,
                kind=kind,
                content=content,
                confidence=0.8 if kind == "suggestion" else 1.0,
            )


async def create_idle_conversation_summaries(
    *,
    now: datetime | None = None,
    agent: SharedMemoryAgent | None = None,
) -> list[SharedConversationSummary]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    memory_agent = agent or SharedMemoryAgent()
    async with AsyncSessionLocal() as session:
        batches = await SharedMemoryTools.idle_conversation_batches(
            session,
            now=current,
            idle_minutes=settings.CHAT_SUMMARY_IDLE_MINUTES,
            message_limit=settings.CHAT_SUMMARY_MAX_MESSAGES,
        )

    summaries: list[SharedConversationSummary] = []
    for batch in batches:
        messages = batch["messages"]
        user_messages = [message for message in messages if message.role == "user"]
        if not user_messages:
            continue
        structured_data = await memory_agent.summarize_messages(
            [
                {
                    "author": message.author_name,
                    "content": message.content,
                }
                for message in user_messages
            ]
        )
        async with AsyncSessionLocal() as session:
            transactions = await FinanceTools.get_expenses_in_window(
                session,
                owner_id=batch["household_id"],
                date_from=messages[0].created_at - timedelta(minutes=1),
                date_to=messages[-1].created_at + timedelta(minutes=5),
            )
        structured_data["money"] = [
            (
                f"{transaction['amount']} {transaction['currency']} — "
                f"{transaction['merchant']} · "
                f"{_CATEGORY_LABELS.get(transaction['category'], transaction['category'])} · "
                + {
                    "synced": (
                        "Google Sheets подтверждён"
                        + (
                            f" ({transaction['sheets_updated_range']})"
                            if transaction.get("sheets_updated_range")
                            else ""
                        )
                    ),
                    "pending": "Google Sheets ожидает синхронизации",
                    "syncing": "Google Sheets синхронизируется",
                    "failed": "Google Sheets: ошибка",
                    "disabled": "Google Sheets не применялся",
                }.get(transaction.get("sheets_status", ""), "статус Google Sheets неизвестен")
            )
            for transaction in transactions
        ]
        summary_text = format_summary(structured_data)
        is_meaningful = any(structured_data.values())
        period_key = str(messages[-1].id)
        async with AsyncSessionLocal.begin() as session:
            if await SharedMemoryTools.summary_exists(
                session,
                household_id=batch["household_id"],
                summary_kind="conversation",
                period_key=period_key,
            ):
                continue
            summary = await SharedMemoryTools.save_summary(
                session,
                household_id=batch["household_id"],
                telegram_chat_id=batch["telegram_chat_id"],
                summary_kind="conversation",
                period_key=period_key,
                summary_text=summary_text,
                structured_data=structured_data,
                window_started_at=messages[0].created_at,
                window_ended_at=messages[-1].created_at,
                source_messages=messages,
            )
            await _remember_summary_items(
                session,
                household_id=batch["household_id"],
                source_message_id=messages[-1].id,
                structured_data=structured_data,
            )
            if not is_meaningful:
                summary.delivery_status = "delivered"
                summary.delivered_at = current
        if is_meaningful:
            summaries.append(summary)
    return summaries


def _merge_summary_data(summaries: list[SharedConversationSummary]) -> dict[str, list[str]]:
    keys = ("decisions", "actions", "money", "open_questions", "facts", "suggestions")
    merged: dict[str, list[str]] = {key: [] for key in keys}
    for summary in summaries:
        for key in keys:
            raw_items = summary.structured_data.get(key, [])
            if not isinstance(raw_items, list):
                continue
            for item in raw_items:
                if isinstance(item, str) and item not in merged[key]:
                    merged[key].append(item)
    return merged


async def create_daily_summaries(
    *,
    now: datetime | None = None,
    agent: SharedMemoryAgent | None = None,
) -> list[SharedConversationSummary]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware.")
    if settings.FAMILY_GROUP_CHAT_ID is None:
        return []

    async with AsyncSessionLocal() as session:
        households = await SharedMemoryTools.shared_households(session)

    memory_agent = agent or SharedMemoryAgent()
    created: list[SharedConversationSummary] = []
    for household in households:
        zone = ZoneInfo(household.timezone or "Europe/Kyiv")
        local_now = current.astimezone(zone)
        # Daily digests use completed 24-hour windows between evening cutoffs.
        # This prevents messages sent after the 21:00 run from disappearing
        # between two calendar-day summaries.
        cutoff_date = local_now.date()
        cutoff = datetime.combine(
            cutoff_date,
            time(hour=settings.CHAT_SUMMARY_DAILY_HOUR),
            tzinfo=zone,
        )
        if local_now < cutoff:
            cutoff -= timedelta(days=1)
        local_start = cutoff - timedelta(days=1)
        local_end = cutoff
        period_key = cutoff.isoformat()
        async with AsyncSessionLocal() as session:
            if await SharedMemoryTools.summary_exists(
                session,
                household_id=household.id,
                summary_kind="daily",
                period_key=period_key,
            ):
                continue
            messages: list = []
            offset = 0
            while True:
                batch = await SharedMemoryTools.messages_in_window(
                    session,
                    household_id=household.id,
                    telegram_chat_id=settings.FAMILY_GROUP_CHAT_ID,
                    start_at=local_start,
                    end_at=local_end,
                    limit=settings.CHAT_SUMMARY_MAX_MESSAGES,
                    offset=offset,
                )
                messages.extend(batch)
                if len(batch) < settings.CHAT_SUMMARY_MAX_MESSAGES:
                    break
                offset += len(batch)
        if not messages:
            continue
        structured_data: dict[str, list[str]] = {
            "decisions": [],
            "actions": [],
            "money": [],
            "open_questions": [],
            "facts": [],
            "suggestions": [],
        }
        for offset in range(0, len(messages), settings.CHAT_SUMMARY_MAX_MESSAGES):
            chunk = messages[offset : offset + settings.CHAT_SUMMARY_MAX_MESSAGES]
            chunk_data = await memory_agent.summarize_messages(
                [{"author": message.author_name, "content": message.content} for message in chunk]
            )
            for key in structured_data:
                for item in chunk_data.get(key, []):
                    if item not in structured_data[key]:
                        structured_data[key].append(item)
        async with AsyncSessionLocal.begin() as session:
            if await SharedMemoryTools.summary_exists(
                session,
                household_id=household.id,
                summary_kind="daily",
                period_key=period_key,
            ):
                continue
            summary = await SharedMemoryTools.save_summary(
                session,
                household_id=household.id,
                telegram_chat_id=settings.FAMILY_GROUP_CHAT_ID,
                summary_kind="daily",
                period_key=period_key,
                summary_text=format_summary(structured_data, daily=True),
                structured_data=structured_data,
                window_started_at=local_start,
                window_ended_at=local_end,
                source_messages=messages,
            )
        created.append(summary)
    return created


async def deliver_due_shared_summaries(
    bot_instance: Bot,
    *,
    now: datetime | None = None,
    agent: SharedMemoryAgent | None = None,
) -> int:
    if not settings.SHARED_CHAT_MEMORY_ENABLED:
        return 0
    async with AsyncSessionLocal() as session:
        existing = await SharedMemoryTools.undelivered_summaries(session)
    summaries = await create_idle_conversation_summaries(now=now, agent=agent)
    summaries.extend(await create_daily_summaries(now=now, agent=agent))
    seen_ids = {summary.id for summary in existing}
    summaries = existing + [summary for summary in summaries if summary.id not in seen_ids]
    delivered = 0
    for summary in summaries:
        if await _deliver_summary(bot_instance, summary):
            delivered += 1
    return delivered


async def run_conversation_summary_worker(bot_instance: Bot) -> None:
    """Generate recaps after inactivity and one shared evening digest."""
    while True:
        try:
            await retry_due_shared_context_messages()
            await deliver_due_shared_summaries(bot_instance)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "Shared-chat summary worker iteration failed (%s).",
                type(error).__name__,
            )
        await asyncio.sleep(settings.CHAT_SUMMARY_POLL_SECONDS)
