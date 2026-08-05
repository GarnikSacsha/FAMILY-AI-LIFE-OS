import hashlib
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models import Household
from app.domains.memory.models import (
    PendingSharedAction,
    SharedConversationMessage,
    SharedConversationMessageRetry,
    SharedConversationSummary,
    SharedMemoryItem,
)

ALLOWED_MESSAGE_TYPES = frozenset({"text", "voice", "photo", "document", "other"})
ALLOWED_MEMORY_KINDS = frozenset({"decision", "action", "money", "open_question", "fact", "preference", "suggestion"})


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime value must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _stored_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo in tests; PostgreSQL returns aware values."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalized_text(value: str, *, max_length: int) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError("Text cannot be empty.")
    return normalized[:max_length]


def _fingerprint(kind: str, content: str) -> str:
    normalized = re.sub(r"\s+", " ", content.strip().casefold().replace("ё", "е"))
    return hashlib.sha256(f"{kind}:{normalized}".encode()).hexdigest()


class SharedMemoryTools:
    """PostgreSQL-backed context limited to the authorized shared family chat."""

    @staticmethod
    async def record_message(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        author_user_id: uuid.UUID | None,
        telegram_chat_id: int,
        telegram_message_id: int | None,
        role: str,
        author_name: str,
        message_type: str,
        content: str,
    ) -> SharedConversationMessage:
        if role not in {"user", "assistant"}:
            raise ValueError("Shared message role must be user or assistant.")
        if message_type not in ALLOWED_MESSAGE_TYPES:
            raise ValueError("Unsupported shared message type.")
        if telegram_message_id is not None:
            existing = await session.execute(
                select(SharedConversationMessage).where(
                    SharedConversationMessage.telegram_chat_id == telegram_chat_id,
                    SharedConversationMessage.telegram_message_id == telegram_message_id,
                    SharedConversationMessage.role == role,
                )
            )
            prior_message = existing.scalar_one_or_none()
            if prior_message is not None:
                return prior_message
        message = SharedConversationMessage(
            household_id=household_id,
            author_user_id=author_user_id,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            role=role,
            author_name=_normalized_text(author_name, max_length=100),
            message_type=message_type,
            content=content.strip()[:20_000],
        )
        if not message.content:
            raise ValueError("Shared message content cannot be empty.")
        session.add(message)
        await session.flush()
        return message

    @staticmethod
    async def queue_message_for_retry(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        telegram_chat_id: int,
        telegram_message_id: int,
        author_name: str,
        message_type: str,
        content: str,
        now: datetime | None = None,
    ) -> SharedConversationMessageRetry:
        """Persist a delivered assistant reply until it is present in shared context."""
        if message_type not in ALLOWED_MESSAGE_TYPES:
            raise ValueError("Unsupported shared message type.")
        current = _utc(now or datetime.now(timezone.utc))
        existing = await session.execute(
            select(SharedConversationMessageRetry).where(
                SharedConversationMessageRetry.telegram_chat_id == telegram_chat_id,
                SharedConversationMessageRetry.telegram_message_id == telegram_message_id,
            )
        )
        retry = existing.scalar_one_or_none()
        if retry is not None:
            return retry
        retry = SharedConversationMessageRetry(
            household_id=household_id,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            author_name=_normalized_text(author_name, max_length=100),
            message_type=message_type,
            content=content.strip()[:20_000],
            next_attempt_at=current,
        )
        if not retry.content:
            raise ValueError("Shared message content cannot be empty.")
        session.add(retry)
        await session.flush()
        return retry

    @staticmethod
    async def claim_due_message_retries(
        session: AsyncSession,
        *,
        now: datetime | None = None,
        limit: int = 20,
        lease_seconds: int = 60,
    ) -> list[SharedConversationMessageRetry]:
        """Lease pending retries so concurrent workers do not replay one reply twice."""
        current = _utc(now or datetime.now(timezone.utc))
        due = or_(
            and_(
                SharedConversationMessageRetry.status == "pending",
                SharedConversationMessageRetry.next_attempt_at <= current,
            ),
            and_(
                SharedConversationMessageRetry.status == "processing",
                SharedConversationMessageRetry.lease_expires_at <= current,
            ),
        )
        result = await session.execute(
            select(SharedConversationMessageRetry)
            .where(due)
            .order_by(
                SharedConversationMessageRetry.next_attempt_at,
                SharedConversationMessageRetry.id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        retries = list(result.scalars().all())
        for retry in retries:
            retry.status = "processing"
            retry.attempt_count += 1
            retry.lease_expires_at = current + timedelta(seconds=lease_seconds)
        await session.flush()
        return retries

    @staticmethod
    async def mark_message_retry_delivered(
        session: AsyncSession,
        *,
        retry_id: uuid.UUID,
    ) -> None:
        retry = await session.get(SharedConversationMessageRetry, retry_id)
        if retry is None:
            return
        retry.status = "delivered"
        retry.lease_expires_at = None
        retry.last_error = None
        await session.flush()

    @staticmethod
    async def reschedule_message_retry(
        session: AsyncSession,
        *,
        retry_id: uuid.UUID,
        error_code: str,
        now: datetime | None = None,
    ) -> None:
        retry = await session.get(SharedConversationMessageRetry, retry_id)
        if retry is None:
            return
        current = _utc(now or datetime.now(timezone.utc))
        retry.status = "pending"
        retry.lease_expires_at = None
        retry.last_error = error_code[:100]
        retry.next_attempt_at = current + timedelta(seconds=min(2**retry.attempt_count, 300))
        await session.flush()

    @staticmethod
    async def recent_context(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        telegram_chat_id: int,
        message_limit: int = 24,
        memory_limit: int = 20,
    ) -> dict[str, list[dict[str, str]]]:
        messages_result = await session.execute(
            select(SharedConversationMessage)
            .where(
                SharedConversationMessage.household_id == household_id,
                SharedConversationMessage.telegram_chat_id == telegram_chat_id,
            )
            .order_by(
                SharedConversationMessage.created_at.desc(),
                SharedConversationMessage.id.desc(),
            )
            .limit(message_limit)
        )
        messages = list(reversed(messages_result.scalars().all()))

        memory_result = await session.execute(
            select(SharedMemoryItem)
            .where(
                SharedMemoryItem.household_id == household_id,
                SharedMemoryItem.status == "active",
            )
            .order_by(SharedMemoryItem.updated_at.desc(), SharedMemoryItem.id.desc())
            .limit(memory_limit)
        )
        memories = memory_result.scalars().all()
        return {
            "messages": [
                {
                    "role": message.role,
                    "author": message.author_name,
                    "content": message.content,
                }
                for message in messages
            ],
            "memories": [
                {
                    "kind": item.kind,
                    "content": item.content,
                }
                for item in memories
            ],
        }

    @staticmethod
    async def remember(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        kind: str,
        content: str,
        source_message_id: uuid.UUID | None = None,
        confidence: float = 1.0,
    ) -> SharedMemoryItem:
        if kind not in ALLOWED_MEMORY_KINDS:
            raise ValueError("Unsupported shared-memory kind.")
        normalized = _normalized_text(content, max_length=2_000)
        fingerprint = _fingerprint(kind, normalized)
        result = await session.execute(
            select(SharedMemoryItem).where(
                SharedMemoryItem.household_id == household_id,
                SharedMemoryItem.fingerprint == fingerprint,
            )
        )
        item = result.scalar_one_or_none()
        if item is None:
            item = SharedMemoryItem(
                household_id=household_id,
                source_message_id=source_message_id,
                kind=kind,
                content=normalized,
                fingerprint=fingerprint,
                confidence=max(0.0, min(float(confidence), 1.0)),
            )
            session.add(item)
        else:
            item.content = normalized
            item.status = "active"
            item.confidence = max(item.confidence, max(0.0, min(float(confidence), 1.0)))
            if source_message_id is not None:
                item.source_message_id = source_message_id
        await session.flush()
        return item

    @staticmethod
    async def dismiss_matching(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        query: str,
        limit: int = 20,
    ) -> list[SharedMemoryItem]:
        """Dismiss active shared memories whose meaningful words overlap a request."""
        matches = await SharedMemoryTools.find_dismiss_matches(
            session,
            household_id=household_id,
            query=query,
            limit=limit,
        )
        for item in matches:
            item.status = "dismissed"
        await session.flush()
        return matches

    @staticmethod
    async def find_dismiss_matches(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        query: str,
        limit: int = 20,
    ) -> list[SharedMemoryItem]:
        """Find dismissible memories without mutating them."""
        result = await session.execute(
            select(SharedMemoryItem)
            .where(
                SharedMemoryItem.household_id == household_id,
                SharedMemoryItem.status == "active",
            )
            .order_by(SharedMemoryItem.updated_at.desc(), SharedMemoryItem.id.desc())
            .limit(100)
        )
        items = list(result.scalars().all())
        query_words = {word for word in re.findall(r"[a-zа-яіїєё0-9]+", query.casefold()) if len(word) >= 4}
        ranked: list[tuple[int, SharedMemoryItem]] = []
        for item in items:
            item_words = set(re.findall(r"[a-zа-яіїєё0-9]+", item.content.casefold()))
            score = len(query_words & item_words)
            if score:
                ranked.append((score, item))
        if not ranked:
            return []
        best_score = max(score for score, _ in ranked)
        matches = [item for score, item in ranked if score == best_score][:limit]
        return matches

    @staticmethod
    async def dismiss_by_ids(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        item_ids: list[uuid.UUID],
    ) -> list[SharedMemoryItem]:
        if not item_ids:
            return []
        result = await session.execute(
            select(SharedMemoryItem)
            .where(
                SharedMemoryItem.household_id == household_id,
                SharedMemoryItem.id.in_(item_ids),
                SharedMemoryItem.status == "active",
            )
            .order_by(SharedMemoryItem.updated_at.desc(), SharedMemoryItem.id.desc())
        )
        matches = list(result.scalars().all())
        for item in matches:
            item.status = "dismissed"
        await session.flush()
        return matches

    @staticmethod
    async def create_pending_reminder(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        telegram_chat_id: int,
        initiated_by_user_id: uuid.UUID,
        title: str,
        now: datetime | None = None,
    ) -> PendingSharedAction:
        current = _utc(now or datetime.now(timezone.utc))
        existing = await SharedMemoryTools.get_pending_action(
            session,
            household_id=household_id,
            telegram_chat_id=telegram_chat_id,
            initiated_by_user_id=initiated_by_user_id,
            now=current,
        )
        if existing is not None:
            existing.status = "cancelled"
        action = PendingSharedAction(
            household_id=household_id,
            telegram_chat_id=telegram_chat_id,
            initiated_by_user_id=initiated_by_user_id,
            action_type="reminder",
            payload={"title": _normalized_text(title, max_length=255)},
            expires_at=current + timedelta(hours=24),
        )
        session.add(action)
        await session.flush()
        return action

    @staticmethod
    async def create_pending_calendar_recurring(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        telegram_chat_id: int,
        initiated_by_user_id: uuid.UUID,
        title: str,
        start_at: datetime,
        timezone_name: str,
        needs_time: bool = False,
        recurrence_end_date: date | None = None,
        now: datetime | None = None,
    ) -> PendingSharedAction:
        current = _utc(now or datetime.now(timezone.utc))
        existing = await SharedMemoryTools.get_pending_action(
            session,
            household_id=household_id,
            telegram_chat_id=telegram_chat_id,
            initiated_by_user_id=initiated_by_user_id,
            now=current,
        )
        if existing is not None:
            existing.status = "cancelled"
        action = PendingSharedAction(
            household_id=household_id,
            telegram_chat_id=telegram_chat_id,
            initiated_by_user_id=initiated_by_user_id,
            action_type="calendar_recurring",
            payload={
                "title": _normalized_text(title, max_length=255),
                "start_at": _utc(start_at).isoformat(),
                "time": start_at.strftime("%H:%M"),
                "timezone_name": timezone_name,
                "needs_time": needs_time,
                "recurrence_end_date": (recurrence_end_date.isoformat() if recurrence_end_date is not None else None),
            },
            expires_at=current + timedelta(hours=24),
        )
        session.add(action)
        await session.flush()
        return action

    @staticmethod
    async def create_pending_calendar_draft(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        telegram_chat_id: int,
        initiated_by_user_id: uuid.UUID,
        draft_payload: dict[str, Any],
        now: datetime | None = None,
    ) -> PendingSharedAction:
        """Stores a validated semantic calendar draft without requiring all schedule fields yet."""

        current = _utc(now or datetime.now(timezone.utc))
        existing = await SharedMemoryTools.get_pending_action(
            session,
            household_id=household_id,
            telegram_chat_id=telegram_chat_id,
            initiated_by_user_id=initiated_by_user_id,
            now=current,
        )
        if existing is not None:
            existing.status = "cancelled"

        recurrence = str(draft_payload.get("recurrence", "none"))
        action_type = "calendar_recurring" if recurrence == "daily" else "calendar_event"
        missing_fields = [
            str(field)
            for field in draft_payload.get("missing_fields", [])
            if str(field) in {"title", "date", "time", "recurrence_end"}
        ]
        raw_title = str(draft_payload.get("title", ""))
        payload = {
            "semantic_draft": True,
            "title": _normalized_text(raw_title, max_length=255) if raw_title.strip() else "",
            "event_date": draft_payload.get("event_date"),
            "time": draft_payload.get("time"),
            "timezone_name": str(draft_payload.get("timezone_name", "Europe/Kyiv"))[:64],
            "recurrence": recurrence if recurrence in {"none", "daily"} else "none",
            "recurrence_end_date": draft_payload.get("recurrence_end_date"),
            "recurring_forever": bool(draft_payload.get("recurring_forever", False)),
            "missing_fields": missing_fields,
        }
        action = PendingSharedAction(
            household_id=household_id,
            telegram_chat_id=telegram_chat_id,
            initiated_by_user_id=initiated_by_user_id,
            action_type=action_type,
            payload=payload,
            expires_at=current + timedelta(hours=24),
        )
        session.add(action)
        await session.flush()
        return action

    @staticmethod
    async def create_pending_calendar_event(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        telegram_chat_id: int,
        initiated_by_user_id: uuid.UUID,
        title: str,
        start_at: datetime,
        timezone_name: str,
        now: datetime | None = None,
    ) -> PendingSharedAction:
        current = _utc(now or datetime.now(timezone.utc))
        existing = await SharedMemoryTools.get_pending_action(
            session,
            household_id=household_id,
            telegram_chat_id=telegram_chat_id,
            initiated_by_user_id=initiated_by_user_id,
            now=current,
        )
        if existing is not None:
            existing.status = "cancelled"
        action = PendingSharedAction(
            household_id=household_id,
            telegram_chat_id=telegram_chat_id,
            initiated_by_user_id=initiated_by_user_id,
            action_type="calendar_event",
            payload={
                "title": _normalized_text(title, max_length=255),
                "start_at": _utc(start_at).isoformat(),
                "timezone_name": timezone_name,
            },
            expires_at=current + timedelta(hours=24),
        )
        session.add(action)
        await session.flush()
        return action

    @staticmethod
    async def get_pending_action(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        telegram_chat_id: int,
        initiated_by_user_id: uuid.UUID,
        now: datetime | None = None,
    ) -> PendingSharedAction | None:
        current = _utc(now or datetime.now(timezone.utc))
        result = await session.execute(
            select(PendingSharedAction)
            .where(
                PendingSharedAction.household_id == household_id,
                PendingSharedAction.telegram_chat_id == telegram_chat_id,
                PendingSharedAction.initiated_by_user_id == initiated_by_user_id,
                PendingSharedAction.status == "pending",
            )
            .order_by(PendingSharedAction.created_at.desc(), PendingSharedAction.id.desc())
            .limit(1)
        )
        action = result.scalar_one_or_none()
        if action is not None and _stored_utc(action.expires_at) <= current:
            action.status = "expired"
            await session.flush()
            return None
        return action

    @staticmethod
    async def complete_pending_action(
        session: AsyncSession,
        *,
        action: PendingSharedAction,
        status: str = "completed",
    ) -> None:
        if status not in {"completed", "cancelled"}:
            raise ValueError("Unsupported pending-action completion status.")
        action.status = status
        await session.flush()

    @staticmethod
    async def idle_conversation_batches(
        session: AsyncSession,
        *,
        now: datetime,
        idle_minutes: int,
        message_limit: int,
    ) -> list[dict[str, Any]]:
        cutoff = _utc(now) - timedelta(minutes=idle_minutes)
        groups = await session.execute(
            select(
                SharedConversationMessage.household_id,
                SharedConversationMessage.telegram_chat_id,
                func.max(SharedConversationMessage.created_at).label("last_at"),
                func.count(SharedConversationMessage.id).label("message_count"),
            )
            .where(SharedConversationMessage.included_in_conversation_summary.is_(False))
            .group_by(
                SharedConversationMessage.household_id,
                SharedConversationMessage.telegram_chat_id,
            )
            .having(
                func.max(SharedConversationMessage.created_at) <= cutoff,
                func.count(SharedConversationMessage.id) >= 2,
            )
        )
        batches: list[dict[str, Any]] = []
        for group in groups.all():
            messages_result = await session.execute(
                select(SharedConversationMessage)
                .where(
                    SharedConversationMessage.household_id == group.household_id,
                    SharedConversationMessage.telegram_chat_id == group.telegram_chat_id,
                    SharedConversationMessage.included_in_conversation_summary.is_(False),
                )
                .order_by(
                    SharedConversationMessage.created_at,
                    SharedConversationMessage.id,
                )
                .limit(message_limit)
            )
            messages = list(messages_result.scalars().all())
            if messages:
                batches.append(
                    {
                        "household_id": group.household_id,
                        "telegram_chat_id": group.telegram_chat_id,
                        "messages": messages,
                    }
                )
        return batches

    @staticmethod
    async def save_summary(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        telegram_chat_id: int,
        summary_kind: str,
        period_key: str,
        summary_text: str,
        structured_data: dict[str, list[str]],
        window_started_at: datetime,
        window_ended_at: datetime,
        source_messages: list[SharedConversationMessage] | None = None,
    ) -> SharedConversationSummary:
        if summary_kind not in {"conversation", "daily"}:
            raise ValueError("Unsupported summary kind.")
        source_messages = source_messages or []
        preserved_summary_text = summary_text.strip()[:10_000]
        if not preserved_summary_text:
            raise ValueError("Summary text cannot be empty.")
        summary = SharedConversationSummary(
            household_id=household_id,
            telegram_chat_id=telegram_chat_id,
            summary_kind=summary_kind,
            period_key=_normalized_text(period_key, max_length=100),
            window_started_at=_stored_utc(window_started_at),
            window_ended_at=_stored_utc(window_ended_at),
            source_last_message_id=source_messages[-1].id if source_messages else None,
            summary_text=preserved_summary_text,
            structured_data=structured_data,
        )
        session.add(summary)
        if source_messages:
            await session.execute(
                update(SharedConversationMessage)
                .where(SharedConversationMessage.id.in_([message.id for message in source_messages]))
                .values(included_in_conversation_summary=True)
            )
        await session.flush()
        return summary

    @staticmethod
    async def mark_summary_delivery(
        session: AsyncSession,
        *,
        summary_id: uuid.UUID,
        delivered_at: datetime | None = None,
        error_code: str | None = None,
    ) -> None:
        summary = await session.get(SharedConversationSummary, summary_id)
        if summary is None:
            return
        if error_code is None:
            summary.delivery_status = "delivered"
            summary.delivered_at = _utc(delivered_at or datetime.now(timezone.utc))
            summary.last_error = None
        else:
            summary.delivery_status = "failed"
            summary.last_error = error_code[:100]
        await session.flush()

    @staticmethod
    async def undelivered_summaries(
        session: AsyncSession,
        *,
        limit: int = 20,
    ) -> list[SharedConversationSummary]:
        result = await session.execute(
            select(SharedConversationSummary)
            .where(SharedConversationSummary.delivery_status.in_(("pending", "failed")))
            .order_by(
                SharedConversationSummary.created_at,
                SharedConversationSummary.id,
            )
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def shared_households(session: AsyncSession) -> list[Household]:
        result = await session.execute(
            select(Household)
            .join(
                SharedConversationMessage,
                SharedConversationMessage.household_id == Household.id,
            )
            .distinct()
        )
        return list(result.scalars().all())

    @staticmethod
    async def summaries_in_window(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        telegram_chat_id: int,
        start_at: datetime,
        end_at: datetime,
        summary_kind: str = "conversation",
    ) -> list[SharedConversationSummary]:
        result = await session.execute(
            select(SharedConversationSummary)
            .where(
                SharedConversationSummary.household_id == household_id,
                SharedConversationSummary.telegram_chat_id == telegram_chat_id,
                SharedConversationSummary.summary_kind == summary_kind,
                SharedConversationSummary.delivery_status == "delivered",
                SharedConversationSummary.window_ended_at >= _utc(start_at),
                SharedConversationSummary.window_ended_at < _utc(end_at),
            )
            .order_by(
                SharedConversationSummary.window_started_at,
                SharedConversationSummary.id,
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def messages_in_window(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        telegram_chat_id: int,
        start_at: datetime,
        end_at: datetime,
        limit: int,
        offset: int = 0,
    ) -> list[SharedConversationMessage]:
        """Return the persisted transcript for one authorized chat and time window."""
        result = await session.execute(
            select(SharedConversationMessage)
            .where(
                SharedConversationMessage.household_id == household_id,
                SharedConversationMessage.telegram_chat_id == telegram_chat_id,
                SharedConversationMessage.created_at >= _utc(start_at),
                SharedConversationMessage.created_at < _utc(end_at),
            )
            .order_by(SharedConversationMessage.created_at, SharedConversationMessage.id)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def summary_exists(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        summary_kind: str,
        period_key: str,
    ) -> bool:
        result = await session.execute(
            select(SharedConversationSummary.id).where(
                SharedConversationSummary.household_id == household_id,
                SharedConversationSummary.summary_kind == summary_kind,
                SharedConversationSummary.period_key == period_key,
            )
        )
        return result.scalar_one_or_none() is not None
