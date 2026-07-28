import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, TimestampMixin


class MemoryItem(Base, TimestampMixin):
    __tablename__ = "memory_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    category: Mapped[str] = mapped_column(String(50), nullable=False)  # 'preference', 'health_note', 'habit'
    fact_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_message: Mapped[str | None] = mapped_column(Text)


class SharedConversationMessage(Base, TimestampMixin):
    """A message from the authorized family group, never from a private chat."""

    __tablename__ = "shared_conversation_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_shared_conversation_messages_role",
        ),
        CheckConstraint(
            "message_type IN ('text', 'voice', 'photo', 'document', 'other')",
            name="ck_shared_conversation_messages_type",
        ),
        UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            "role",
            name="uq_shared_conversation_messages_telegram_identity",
        ),
        Index(
            "ix_shared_conversation_messages_summary_queue",
            "telegram_chat_id",
            "included_in_conversation_summary",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    author_name: Mapped[str] = mapped_column(String(100), nullable=False)
    message_type: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    included_in_conversation_summary: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )


class SharedMemoryItem(Base, TimestampMixin):
    """A structured fact or open loop extracted only from the shared chat."""

    __tablename__ = "shared_memory_items"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('decision', 'action', 'money', 'open_question', 'fact', 'preference', 'suggestion')",
            name="ck_shared_memory_items_kind",
        ),
        CheckConstraint(
            "status IN ('active', 'resolved', 'dismissed')",
            name="ck_shared_memory_items_status",
        ),
        UniqueConstraint(
            "household_id",
            "fingerprint",
            name="uq_shared_memory_items_household_fingerprint",
        ),
        Index(
            "ix_shared_memory_items_active",
            "household_id",
            "status",
            "updated_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shared_conversation_messages.id", ondelete="SET NULL"),
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
        server_default="active",
        nullable=False,
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)


class SharedConversationSummary(Base, TimestampMixin):
    """A persisted conversation recap or daily digest for the family group."""

    __tablename__ = "shared_conversation_summaries"
    __table_args__ = (
        CheckConstraint(
            "summary_kind IN ('conversation', 'daily')",
            name="ck_shared_conversation_summaries_kind",
        ),
        CheckConstraint(
            "delivery_status IN ('pending', 'delivered', 'failed')",
            name="ck_shared_conversation_summaries_delivery_status",
        ),
        UniqueConstraint(
            "household_id",
            "summary_kind",
            "period_key",
            name="uq_shared_conversation_summaries_period",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    summary_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    period_key: Mapped[str] = mapped_column(String(100), nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_last_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shared_conversation_messages.id", ondelete="SET NULL"),
    )
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    structured_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    delivery_status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        server_default="pending",
        nullable=False,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(100))


class PendingSharedAction(Base, TimestampMixin):
    """A multi-turn action awaiting one missing detail in the family group."""

    __tablename__ = "pending_shared_actions"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ('reminder')",
            name="ck_pending_shared_actions_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'cancelled', 'expired')",
            name="ck_pending_shared_actions_status",
        ),
        Index(
            "ix_pending_shared_actions_lookup",
            "household_id",
            "telegram_chat_id",
            "status",
            "expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    initiated_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        server_default="pending",
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
