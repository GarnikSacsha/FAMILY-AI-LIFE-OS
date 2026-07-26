import uuid
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.domains.identity.models import User


class PermissionDeniedError(Exception):
    """Raised when an actor or chat fails authorization policy checks."""

    pass


class ActorContext(BaseModel):
    user_id: uuid.UUID
    telegram_id: int
    household_id: uuid.UUID
    chat_id: int
    chat_type: Literal["private", "group", "supergroup"]
    is_admin: bool = False


class IdentityService:
    """Core Identity & Authorization Service for resolving ActorContext and enforcing security scopes."""

    @classmethod
    async def resolve_actor(
        cls,
        session: AsyncSession,
        telegram_user_id: int,
        chat_id: int,
        chat_type: str,
    ) -> ActorContext:
        """Resolves internal UUID actor context from external Telegram ID and validates chat policies."""
        if chat_type not in ("private", "group", "supergroup"):
            raise PermissionDeniedError("Access denied.")

        allowed_ids = {
            settings.DENYS_TELEGRAM_ID,
            settings.OLEKSANDRA_TELEGRAM_ID,
        }
        if telegram_user_id not in allowed_ids:
            raise PermissionDeniedError("Access denied.")

        # Query user by Telegram ID
        stmt = select(User).where(User.telegram_id == telegram_user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise PermissionDeniedError("Access denied.")

        household_id = user.household_id
        if not household_id:
            raise PermissionDeniedError("Access denied.")

        # Enforce Chat Type Policies
        if chat_type in ("group", "supergroup"):
            # Group chat only allowed if chat_id matches FAMILY_GROUP_CHAT_ID
            if settings.FAMILY_GROUP_CHAT_ID is None or chat_id != settings.FAMILY_GROUP_CHAT_ID:
                raise PermissionDeniedError("Access denied.")

        return ActorContext(
            user_id=user.id,
            telegram_id=user.telegram_id,
            household_id=household_id,
            chat_id=chat_id,
            chat_type=chat_type,  # type: ignore
            is_admin=user.is_admin,
        )

    @classmethod
    def validate_domain_access(cls, actor: ActorContext, domain: str) -> None:
        """Enforces private-only restrictions for sensitive domains."""
        sensitive_domains = {
            "health",
            "oauth",
            "medical_docs",
            "personal_memory",
            "email",
            "calendar",
        }
        if domain in sensitive_domains and actor.chat_type != "private":
            raise PermissionDeniedError("Access denied.")
