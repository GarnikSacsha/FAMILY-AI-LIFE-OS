import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
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
    async def create_reminder(
        session: AsyncSession,
        *,
        recipient_id: uuid.UUID,
        title: str,
        trigger_at: datetime,
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
