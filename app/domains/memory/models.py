import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, TimestampMixin


class MemoryItem(Base, TimestampMixin):
    __tablename__ = "memory_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    category: Mapped[str] = mapped_column(String(50), nullable=False)  # 'preference', 'health_note', 'habit'
    fact_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_message: Mapped[str | None] = mapped_column(Text)
