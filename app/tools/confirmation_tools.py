import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.memory.models import PendingConfirmation

ALLOWED_ACTION_TYPES = frozenset({"finance_log", "calendar_delete", "memory_dismiss"})


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime value must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _stored_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class ConfirmationTools:
    """Persistence and atomic state transitions for dangerous chat operations."""

    @staticmethod
    async def create_or_get(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        telegram_chat_id: int,
        initiated_by_user_id: uuid.UUID,
        action_type: str,
        payload: dict[str, Any],
        request_key: str,
        now: datetime | None = None,
    ) -> PendingConfirmation:
        if action_type not in ALLOWED_ACTION_TYPES:
            raise ValueError("Unsupported confirmation action type.")
        if not request_key or len(request_key) > 200:
            raise ValueError("Invalid confirmation request key.")
        current = _utc(now or datetime.now(timezone.utc))
        existing = await session.execute(
            select(PendingConfirmation).where(
                PendingConfirmation.household_id == household_id,
                PendingConfirmation.telegram_chat_id == telegram_chat_id,
                PendingConfirmation.initiated_by_user_id == initiated_by_user_id,
                PendingConfirmation.request_key == request_key,
            )
        )
        confirmation = existing.scalar_one_or_none()
        if confirmation is not None:
            return confirmation

        try:
            async with session.begin_nested():
                confirmation = PendingConfirmation(
                    household_id=household_id,
                    telegram_chat_id=telegram_chat_id,
                    initiated_by_user_id=initiated_by_user_id,
                    action_type=action_type,
                    payload=payload,
                    confirmation_code=secrets.token_urlsafe(6),
                    request_key=request_key,
                    expires_at=current + timedelta(minutes=15),
                )
                session.add(confirmation)
                await session.flush()
        except IntegrityError:
            # A duplicate Telegram update raced this transaction. Return the
            # already persisted immutable proposal instead of creating another.
            existing = await session.execute(
                select(PendingConfirmation).where(
                    PendingConfirmation.household_id == household_id,
                    PendingConfirmation.telegram_chat_id == telegram_chat_id,
                    PendingConfirmation.initiated_by_user_id == initiated_by_user_id,
                    PendingConfirmation.request_key == request_key,
                )
            )
            confirmation = existing.scalar_one()
        return confirmation

    @staticmethod
    async def find_for_reply(
        session: AsyncSession,
        *,
        household_id: uuid.UUID,
        telegram_chat_id: int,
        initiated_by_user_id: uuid.UUID,
        confirmation_code: str,
        now: datetime | None = None,
    ) -> PendingConfirmation | None:
        current = _utc(now or datetime.now(timezone.utc))
        result = await session.execute(
            select(PendingConfirmation)
            .where(
                PendingConfirmation.household_id == household_id,
                PendingConfirmation.telegram_chat_id == telegram_chat_id,
                PendingConfirmation.initiated_by_user_id == initiated_by_user_id,
                PendingConfirmation.confirmation_code == confirmation_code,
            )
            .with_for_update()
        )
        confirmation = result.scalar_one_or_none()
        if (
            confirmation is not None
            and confirmation.status == "pending"
            and _stored_utc(confirmation.expires_at) <= current
        ):
            confirmation.status = "expired"
            await session.flush()
        return confirmation

    @staticmethod
    async def cancel(confirmation: PendingConfirmation) -> bool:
        if confirmation.status != "pending":
            return False
        confirmation.status = "cancelled"
        return True

    @staticmethod
    async def claim(
        session: AsyncSession,
        confirmation: PendingConfirmation,
        *,
        now: datetime | None = None,
    ) -> bool:
        current = _utc(now or datetime.now(timezone.utc))
        if confirmation.status != "pending":
            return False
        if _stored_utc(confirmation.expires_at) <= current:
            confirmation.status = "expired"
            return False
        confirmation.status = "executing"
        await session.flush()
        return True

    @staticmethod
    async def complete(confirmation: PendingConfirmation, *, now: datetime | None = None) -> None:
        confirmation.status = "completed"
        confirmation.executed_at = _utc(now or datetime.now(timezone.utc))
        confirmation.last_error = None

    @staticmethod
    async def fail(confirmation: PendingConfirmation, error: Exception) -> None:
        confirmation.status = "failed"
        confirmation.last_error = type(error).__name__[:100]
