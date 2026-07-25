import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models import User, OAuthToken
from app.domains.health.models import Meal, OuraDailyMetric
from app.integrations.gemini.client import GeminiVisionClient
from app.integrations.oura.client import OuraClient


class HealthTools:
    """Deterministic health domain tools adhering to Unit of Work (no explicit commit inside tools)."""

    @staticmethod
    async def save_oura_tokens(
        session: AsyncSession,
        telegram_id: int,
        tokens: Dict[str, Any],
        user_id: Optional[uuid.UUID] = None,
    ) -> bool:
        """Stores or updates Oura OAuth tokens within the active session transaction (uses flush, not commit)."""
        if not user_id and telegram_id > 0:
            stmt = select(User).where(User.telegram_id == telegram_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user:
                user = User(
                    id=uuid.uuid4(),
                    telegram_id=telegram_id,
                    first_name="User",
                )
                session.add(user)
                await session.flush()
            user_id = user.id

        if not user_id:
            raise ValueError("save_oura_tokens requires a valid user_id or registered telegram_id.")

        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token")
        expires_at = datetime.now(timezone.utc)

        token_stmt = select(OAuthToken).where(
            OAuthToken.user_id == user_id,
            OAuthToken.provider == "oura"
        )
        token_res = await session.execute(token_stmt)
        oauth_record = token_res.scalar_one_or_none()

        if oauth_record:
            oauth_record.access_token_encrypted = access_token
            oauth_record.refresh_token_encrypted = refresh_token
            oauth_record.expires_at = expires_at
        else:
            oauth_record = OAuthToken(
                user_id=user_id,
                provider="oura",
                access_token_encrypted=access_token,
                refresh_token_encrypted=refresh_token,
                expires_at=expires_at,
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
        storage_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Analyzes a food photo via Gemini Vision and logs the meal into PostgreSQL."""
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
