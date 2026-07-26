import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.audit.models import AuditLog


class AuditService:
    ALLOWED_CHAT_TYPES = frozenset({"private", "group", "supergroup", "system"})
    ALLOWED_RESULTS = frozenset({"success", "denied", "error"})

    @classmethod
    async def record(
        cls,
        session: AsyncSession,
        *,
        request_id: uuid.UUID,
        actor_user_id: uuid.UUID | None,
        chat_type: str,
        action: str,
        target_domain: str,
        result: str,
        target_entity_id: uuid.UUID | None = None,
        error_code: str | None = None,
    ) -> AuditLog:
        if chat_type not in cls.ALLOWED_CHAT_TYPES:
            raise ValueError("Unsupported audit chat type.")
        if result not in cls.ALLOWED_RESULTS:
            raise ValueError("Unsupported audit result.")
        action = action.strip()
        target_domain = target_domain.strip()
        error_code = error_code.strip() if error_code else None
        if not action or len(action) > 100:
            raise ValueError("Invalid audit action.")
        if not target_domain or len(target_domain) > 50:
            raise ValueError("Invalid audit target domain.")
        if error_code and len(error_code) > 100:
            raise ValueError("Invalid audit error code.")

        event = AuditLog(
            request_id=request_id,
            actor_user_id=actor_user_id,
            chat_type=chat_type,
            action=action,
            target_domain=target_domain,
            target_entity_id=target_entity_id,
            result=result,
            error_code=error_code,
        )
        session.add(event)
        await session.flush()
        return event

    @classmethod
    async def record_after_rollback(
        cls,
        session_factory: async_sessionmaker[AsyncSession],
        **event: object,
    ) -> None:
        """Persist a denial/error independently after the business transaction rolled back."""
        async with session_factory.begin() as session:
            await cls.record(session, **event)  # type: ignore[arg-type]
