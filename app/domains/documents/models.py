import uuid

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, TimestampMixin


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)  # 'medical_report', 'receipt', 'contract'
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(64))

    extracted_facts: Mapped[dict | None] = mapped_column(JSON)
    processing_status: Mapped[str] = mapped_column(String(30), default="PROCESSED")
