import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, Float, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, TimestampMixin


class FinancialTransaction(Base, TimestampMixin):
    __tablename__ = "financial_transactions"
    __table_args__ = (
        CheckConstraint("owner_type IN ('user', 'household')", name="ck_financial_transactions_owner_type"),
        CheckConstraint("amount > 0", name="ck_financial_transactions_amount_positive"),
        CheckConstraint("direction IN ('expense', 'income')", name="ck_financial_transactions_direction"),
        CheckConstraint(
            "length(currency) = 3 AND currency = upper(currency)",
            name="ck_financial_transactions_currency",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_financial_transactions_confidence",
        ),
        CheckConstraint(
            "sheets_sync_status IN ('pending', 'syncing', 'synced', 'failed', 'disabled')",
            name="ck_financial_transactions_sheets_sync_status",
        ),
        Index(
            "uq_financial_transactions_import_identity",
            "source",
            "account_id",
            "owner_type",
            "owner_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
            sqlite_where=text("external_id IS NOT NULL"),
        ),
        Index("ix_financial_transactions_owner_occurred", "owner_id", "occurred_at"),
        Index(
            "ix_financial_transactions_sheets_sync",
            "sheets_sync_status",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'user' or 'household'
    owner_id: Mapped[uuid.UUID] = mapped_column(nullable=False)

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    merchant: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))

    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="UAH")
    direction: Mapped[str] = mapped_column(String(10), default="expense")  # 'expense' or 'income'

    category: Mapped[str] = mapped_column(String(100), nullable=False, default="Uncategorized")
    subcategory: Mapped[str | None] = mapped_column(String(100))

    source: Mapped[str] = mapped_column(String(50), default="manual")  # 'receipt', 'bank_api', 'manual'
    account_id: Mapped[str] = mapped_column(String(255), default="default", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    external_id: Mapped[str | None] = mapped_column(String(255))

    receipt_storage_key: Mapped[str | None] = mapped_column(String(255))
    raw_metadata: Mapped[dict | None] = mapped_column(JSON)

    sheets_sync_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    sheets_sync_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sheets_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sheets_next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sheets_sync_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sheets_failure_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sheets_sync_error: Mapped[str | None] = mapped_column(String(100))
    sheets_updated_range: Mapped[str | None] = mapped_column(String(255))
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
