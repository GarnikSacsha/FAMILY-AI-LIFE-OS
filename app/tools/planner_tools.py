import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.planning.models import Reminder, ShoppingItem, Task


class PlannerTools:
    """Deterministic planning tools for tasks, shopping items, and reminders."""

    @staticmethod
    async def create_task(
        session: AsyncSession,
        creator_id: uuid.UUID,
        owner_type: str,
        owner_id: uuid.UUID,
        title: str,
        assignee_id: uuid.UUID | None = None,
        due_date: datetime | None = None,
    ) -> dict[str, Any]:
        """Creates a new task in PostgreSQL."""
        task = Task(
            creator_id=creator_id,
            owner_type=owner_type,
            owner_id=owner_id,
            assignee_id=assignee_id or creator_id,
            title=title,
            due_date=due_date,
        )
        session.add(task)
        await session.flush()

        return {
            "task_id": str(task.id),
            "title": task.title,
            "status": "CREATED",
        }

    @staticmethod
    async def get_active_tasks(
        session: AsyncSession,
        *,
        owner_id: uuid.UUID,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return unfinished tasks from PostgreSQL in due-date order."""
        stmt = (
            select(Task)
            .where(
                Task.owner_id == owner_id,
                Task.is_completed.is_(False),
            )
            .order_by(Task.due_date.asc().nullslast(), Task.created_at, Task.id)
            .limit(limit)
        )
        result = await session.execute(stmt)
        return [
            {
                "task_id": str(task.id),
                "title": task.title,
                "due_date": task.due_date.isoformat() if task.due_date else None,
            }
            for task in result.scalars().all()
        ]

    @staticmethod
    async def create_reminder(
        session: AsyncSession,
        *,
        recipient_id: uuid.UUID,
        title: str,
        trigger_at: datetime,
        telegram_chat_id: int | None = None,
    ) -> dict[str, Any]:
        normalized_title = " ".join(title.strip().split())
        if not normalized_title:
            raise ValueError("Reminder title cannot be empty.")
        if len(normalized_title) > 255:
            raise ValueError("Reminder title exceeds 255 characters.")
        if trigger_at.tzinfo is None or trigger_at.utcoffset() is None:
            raise ValueError("Reminder time must be timezone-aware.")
        reminder = Reminder(
            recipient_id=recipient_id,
            telegram_chat_id=telegram_chat_id,
            title=normalized_title,
            trigger_at=trigger_at,
        )
        session.add(reminder)
        await session.flush()
        return {
            "reminder_id": str(reminder.id),
            "title": reminder.title,
            "trigger_at": reminder.trigger_at.isoformat(),
            "status": "CREATED",
        }

    @staticmethod
    async def add_shopping_item(
        session: AsyncSession,
        household_id: uuid.UUID,
        added_by_id: uuid.UUID,
        item_name: str,
        quantity: str | None = None,
    ) -> dict[str, Any]:
        """Adds an item to the household shopping list."""
        item = ShoppingItem(
            household_id=household_id,
            added_by_id=added_by_id,
            item_name=item_name,
            quantity=quantity,
        )
        session.add(item)
        await session.flush()

        return {
            "shopping_item_id": str(item.id),
            "item_name": item.item_name,
            "status": "ADDED",
        }

    @staticmethod
    async def get_active_shopping_list(session: AsyncSession, household_id: uuid.UUID) -> list[dict[str, Any]]:
        """Retrieves non-purchased shopping items for a household."""
        stmt = select(ShoppingItem).where(
            ShoppingItem.household_id == household_id,
            ShoppingItem.is_purchased.is_(False),
        )
        result = await session.execute(stmt)
        items = result.scalars().all()

        return [{"id": str(i.id), "item_name": i.item_name, "quantity": i.quantity} for i in items]

    @staticmethod
    async def create_reminders(
        session: AsyncSession,
        *,
        recipient_id: uuid.UUID,
        telegram_chat_id: int,
        title: str,
        trigger_times: tuple[datetime, ...],
    ) -> list[dict[str, Any]]:
        """Persist one or more reminders that will be delivered to the source chat."""
        if not title.strip():
            raise ValueError("Reminder title cannot be empty.")
        if len(title) > 255:
            raise ValueError("Reminder title is too long.")
        if not trigger_times:
            raise ValueError("At least one trigger time is required.")

        reminders: list[Reminder] = []
        for trigger_at in trigger_times:
            if trigger_at.tzinfo is None:
                raise ValueError("Reminder trigger time must be timezone-aware.")
            reminder = Reminder(
                recipient_id=recipient_id,
                telegram_chat_id=telegram_chat_id,
                title=title.strip(),
                trigger_at=trigger_at.astimezone(timezone.utc),
            )
            session.add(reminder)
            reminders.append(reminder)
        await session.flush()
        return [
            {
                "reminder_id": str(reminder.id),
                "title": reminder.title,
                "trigger_at": reminder.trigger_at,
                "status": "CREATED",
            }
            for reminder in reminders
        ]

    @staticmethod
    async def claim_due_reminders(
        session: AsyncSession,
        *,
        now: datetime,
        limit: int = 50,
        lease_seconds: int = 300,
    ) -> list[dict[str, Any]]:
        """Atomically lease due reminders so concurrent workers do not send the same row."""
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware.")
        utc_now = now.astimezone(timezone.utc)
        expired_claim = utc_now - timedelta(seconds=lease_seconds)
        ready_to_retry = and_(
            Reminder.delivery_status.in_(("pending", "failed")),
            or_(Reminder.next_attempt_at.is_(None), Reminder.next_attempt_at <= utc_now),
        )
        abandoned_delivery = and_(
            Reminder.delivery_status == "sending",
            Reminder.claimed_at <= expired_claim,
        )
        stmt = (
            select(Reminder)
            .where(
                Reminder.is_triggered.is_(False),
                Reminder.telegram_chat_id.is_not(None),
                Reminder.trigger_at <= utc_now,
                or_(ready_to_retry, abandoned_delivery),
            )
            .order_by(Reminder.trigger_at, Reminder.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await session.execute(stmt)
        reminders = list(result.scalars().all())
        for reminder in reminders:
            reminder.delivery_status = "sending"
            reminder.delivery_attempts += 1
            reminder.claimed_at = utc_now
            reminder.next_attempt_at = None
            reminder.last_error = None
        await session.flush()
        return [
            {
                "reminder_id": reminder.id,
                "telegram_chat_id": reminder.telegram_chat_id,
                "title": reminder.title,
            }
            for reminder in reminders
        ]

    @staticmethod
    async def mark_reminder_delivered(
        session: AsyncSession,
        *,
        reminder_id: uuid.UUID,
        delivered_at: datetime,
    ) -> None:
        reminder = await session.get(Reminder, reminder_id)
        if reminder is None:
            return
        reminder.is_triggered = True
        reminder.delivery_status = "delivered"
        reminder.delivered_at = delivered_at.astimezone(timezone.utc)
        reminder.claimed_at = None
        reminder.next_attempt_at = None
        reminder.last_error = None
        await session.flush()

    @staticmethod
    async def mark_reminder_failed(
        session: AsyncSession,
        *,
        reminder_id: uuid.UUID,
        failed_at: datetime,
        error_code: str,
    ) -> None:
        reminder = await session.get(Reminder, reminder_id)
        if reminder is None or reminder.is_triggered:
            return
        retry_seconds = min(300, 5 * (2 ** min(reminder.delivery_attempts - 1, 6)))
        reminder.delivery_status = "failed"
        reminder.claimed_at = None
        reminder.next_attempt_at = failed_at.astimezone(timezone.utc) + timedelta(seconds=retry_seconds)
        reminder.last_error = error_code[:100]
        await session.flush()
