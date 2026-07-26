import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, TimestampMixin


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "owner_type IN ('user', 'household')",
            name="ck_tasks_owner_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'user' or 'household'
    owner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)

    creator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ShoppingItem(Base, TimestampMixin):
    __tablename__ = "shopping_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("households.id"), nullable=False, index=True)
    added_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[str | None] = mapped_column(String(50))
    is_purchased: Mapped[bool] = mapped_column(Boolean, default=False)


class Reminder(Base, TimestampMixin):
    __tablename__ = "reminders"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    recipient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    is_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    quiet_hours_override: Mapped[bool] = mapped_column(Boolean, default=False)
