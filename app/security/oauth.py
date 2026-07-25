import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models import OAuthAuthorizationState


class OAuthStateError(Exception):
    """Base exception for OAuth state generation or verification failures."""
    pass


class OAuthStateManager:
    """Manages cryptographically secure 128-bit OAuth state hashes with 10-minute TTL."""

    STATE_TTL_MINUTES = 10

    @staticmethod
    def _hash_state(raw_state: str) -> str:
        """Computes SHA-256 hash of raw state to prevent plaintext leakage in DB."""
        return hashlib.sha256(raw_state.encode("utf-8")).hexdigest()

    @classmethod
    async def create_state(
        cls, session: AsyncSession, user_id: uuid.UUID, provider: str = "oura"
    ) -> Tuple[str, OAuthAuthorizationState]:
        """Generates 256-bit entropy random state string, saves state hash to DB, returns (raw_state, db_record)."""
        raw_state = secrets.token_urlsafe(32)  # 256 bits of entropy
        state_hash = cls._hash_state(raw_state)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=cls.STATE_TTL_MINUTES)

        db_state = OAuthAuthorizationState(
            id=uuid.uuid4(),
            state_hash=state_hash,
            user_id=user_id,
            provider=provider,
            expires_at=expires_at,
        )

        session.add(db_state)
        await session.flush()

        return raw_state, db_state

    @classmethod
    async def validate_and_consume_state(
        cls, session: AsyncSession, raw_state: str, provider: str = "oura"
    ) -> uuid.UUID:
        """Validates raw state hash, checks expiration and single-use, consumes state, returns user_id."""
        if not raw_state or not raw_state.strip():
            raise OAuthStateError("OAuth state parameter is missing or empty.")

        state_hash = cls._hash_state(raw_state)
        stmt = select(OAuthAuthorizationState).where(
            OAuthAuthorizationState.state_hash == state_hash,
            OAuthAuthorizationState.provider == provider,
        )
        result = await session.execute(stmt)
        db_state = result.scalar_one_or_none()

        if not db_state:
            raise OAuthStateError("Invalid OAuth state. Potential CSRF or unauthorized callback.")

        if db_state.consumed_at is not None:
            raise OAuthStateError("OAuth state has already been consumed. Replay attack detected.")

        now = datetime.now(timezone.utc)
        if db_state.expires_at.tzinfo is None:
            db_state.expires_at = db_state.expires_at.replace(tzinfo=timezone.utc)

        if now > db_state.expires_at:
            raise OAuthStateError("OAuth state has expired. Please initiate authorization again.")

        # Mark consumed
        db_state.consumed_at = now
        await session.flush()

        return db_state.user_id
