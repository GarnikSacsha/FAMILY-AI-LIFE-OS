import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.planning.models import Task, ShoppingItem, Reminder


class PlannerTools:
    """Deterministic planning tools for tasks, shopping items, and reminders."""

    @staticmethod
    async def create_task(
        session: AsyncSession,
        creator_id: uuid.UUID,
        owner_type: str,
        owner_id: uuid.UUID,
        title: str,
        assignee_id: Optional[uuid.UUID] = None,
        due_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
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
    async def add_shopping_item(
        session: AsyncSession,
        household_id: uuid.UUID,
        added_by_id: uuid.UUID,
        item_name: str,
        quantity: Optional[str] = None,
    ) -> Dict[str, Any]:
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
    async def get_active_shopping_list(
        session: AsyncSession, household_id: uuid.UUID
    ) -> List[Dict[str, Any]]:
        """Retrieves non-purchased shopping items for a household."""
        stmt = select(ShoppingItem).where(
            ShoppingItem.household_id == household_id,
            ShoppingItem.is_purchased == False,
        )
        result = await session.execute(stmt)
        items = result.scalars().all()

        return [
            {"id": str(i.id), "item_name": i.item_name, "quantity": i.quantity}
            for i in items
        ]
