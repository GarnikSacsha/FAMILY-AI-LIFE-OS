import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Float, ForeignKey, JSON, DateTime, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, TimestampMixin


class FinancialTransaction(Base, TimestampMixin):
    __tablename__ = "financial_transactions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'user' or 'household'
    owner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    merchant: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500))
    
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="UAH")
    direction: Mapped[str] = mapped_column(String(10), default="expense")  # 'expense' or 'income'
    
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="Uncategorized")
    subcategory: Mapped[Optional[str]] = mapped_column(String(100))
    
    source: Mapped[str] = mapped_column(String(50), default="manual")  # 'receipt', 'bank_api', 'manual'
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)
    
    receipt_storage_key: Mapped[Optional[str]] = mapped_column(String(255))
    raw_metadata: Mapped[Optional[dict]] = mapped_column(JSON)
