import uuid
from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy import String, BigInteger, Boolean, ForeignKey, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, TimestampMixin


class Household(Base, TimestampMixin):
    __tablename__ = "households"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="Family Workspace")
    description: Mapped[Optional[str]] = mapped_column(String(255))

    members: Mapped[List["User"]] = relationship(back_populates="household")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    household_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("households.id", ondelete="SET NULL"))
    
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[Optional[str]] = mapped_column(String(100))
    username: Mapped[Optional[str]] = mapped_column(String(100))
    
    timezone: Mapped[str] = mapped_column(String(50), default="Europe/Kiev")
    language: Mapped[str] = mapped_column(String(10), default="ru")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    household: Mapped[Optional["Household"]] = relationship(back_populates="members")
    oauth_tokens: Mapped[List["OAuthToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class OAuthAuthorizationState(Base, TimestampMixin):
    __tablename__ = "oauth_authorization_states"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="oura")
    
    code_verifier_encrypted: Mapped[Optional[str]] = mapped_column(String(500))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class OAuthToken(Base, TimestampMixin):
    __tablename__ = "oauth_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # 'oura', 'google', etc.
    access_token_encrypted: Mapped[str] = mapped_column(String(500), nullable=False)
    refresh_token_encrypted: Mapped[Optional[str]] = mapped_column(String(500))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    scope: Mapped[Optional[str]] = mapped_column(String(255))

    user: Mapped["User"] = relationship(back_populates="oauth_tokens")
