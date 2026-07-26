import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class AuditLog(Base):
    """Append-only security audit event. Message and payload contents are never stored."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(
            "chat_type IN ('private', 'group', 'supergroup', 'system')",
            name="ck_audit_logs_chat_type",
        ),
        CheckConstraint(
            "result IN ('success', 'denied', 'error')",
            name="ck_audit_logs_result",
        ),
        Index("ix_audit_logs_actor_created", "actor_user_id", "created_at"),
        Index("ix_audit_logs_domain_created", "target_domain", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    chat_type: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_domain: Mapped[str] = mapped_column(String(50), nullable=False)
    target_entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
