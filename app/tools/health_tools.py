import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.health.models import Meal
from app.domains.identity.models import OAuthToken
from app.security.token_cipher import TokenCipher, get_token_cipher


class HealthTools:
    """Deterministic health domain tools adhering to Unit of Work (no explicit commit inside tools)."""

    @staticmethod
    async def save_oura_tokens(
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        tokens: Mapping[str, Any],
        cipher: TokenCipher | None = None,
    ) -> bool:
        """Encrypt and persist Oura tokens inside the caller-owned transaction."""
        if not isinstance(user_id, uuid.UUID):
            raise ValueError("save_oura_tokens requires a valid internal user_id.")

        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise ValueError("Oura access token is missing.")
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            raise ValueError("Oura refresh token is missing.")

        expires_in_raw = tokens.get("expires_in")
        if isinstance(expires_in_raw, int) and not isinstance(expires_in_raw, bool):
            expires_in = expires_in_raw
        elif isinstance(expires_in_raw, str) and expires_in_raw.isdigit():
            expires_in = int(expires_in_raw)
        else:
            raise ValueError("Oura expires_in must be a positive integer.")
        if expires_in <= 0:
            raise ValueError("Oura expires_in must be a positive integer.")

        scope_raw = tokens.get("scope")
        if isinstance(scope_raw, (list, tuple, set)):
            scope = " ".join(str(item) for item in scope_raw)
        elif scope_raw is None:
            scope = None
        else:
            scope = str(scope_raw)

        token_cipher = cipher or get_token_cipher()
        encrypted_access_token = token_cipher.encrypt(
            access_token.strip(),
            user_id=user_id,
            provider="oura",
            token_type="access_token",  # noqa: S106  # nosec B106
        )
        encrypted_refresh_token = token_cipher.encrypt(
            refresh_token.strip(),
            user_id=user_id,
            provider="oura",
            token_type="refresh_token",  # noqa: S106  # nosec B106
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        token_stmt = (
            select(OAuthToken)
            .where(
                OAuthToken.user_id == user_id,
                OAuthToken.provider == "oura",
            )
            .with_for_update()
        )
        token_res = await session.execute(token_stmt)
        oauth_record = token_res.scalar_one_or_none()

        if oauth_record:
            oauth_record.access_token_encrypted = encrypted_access_token
            oauth_record.refresh_token_encrypted = encrypted_refresh_token
            oauth_record.expires_at = expires_at
            oauth_record.scope = scope
        else:
            oauth_record = OAuthToken(
                user_id=user_id,
                provider="oura",
                access_token_encrypted=encrypted_access_token,
                refresh_token_encrypted=encrypted_refresh_token,
                expires_at=expires_at,
                scope=scope,
            )
            session.add(oauth_record)

        await session.flush()
        return True

    @staticmethod
    async def log_meal_photo(
        session: AsyncSession,
        user_id: uuid.UUID,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        storage_key: str | None = None,
    ) -> dict[str, Any]:
        """Analyzes a food photo via Gemini Vision and logs the meal into PostgreSQL."""
        from app.integrations.gemini.client import GeminiVisionClient

        analysis = await GeminiVisionClient.analyze_food_photo(image_bytes, mime_type)

        meal = Meal(
            user_id=user_id,
            consumed_at=datetime.now(timezone.utc),
            dish_name=analysis.get("dish_name", "Unknown Dish"),
            ingredients_json={"items": analysis.get("ingredients", [])},
            calories_est=int(analysis.get("calories_est", 0)),
            proteins_g=float(analysis.get("proteins_g", 0.0)),
            fats_g=float(analysis.get("fats_g", 0.0)),
            carbs_g=float(analysis.get("carbs_g", 0.0)),
            confidence_score=float(analysis.get("confidence_score", 0.8)),
            coaching_tip=analysis.get("coaching_tip"),
            image_storage_key=storage_key,
        )

        session.add(meal)
        await session.flush()

        return {
            "meal_id": str(meal.id),
            "dish_name": meal.dish_name,
            "calories_est": meal.calories_est,
            "proteins_g": meal.proteins_g,
            "fats_g": meal.fats_g,
            "carbs_g": meal.carbs_g,
            "coaching_tip": meal.coaching_tip,
            "status": "SUCCESS",
        }
