import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.health.models import Meal, OuraDailyMetric
from app.integrations.gemini.client import GeminiVisionClient
from app.integrations.oura.client import OuraClient


class HealthTools:
    """Deterministic health domain tools for database & API interactions."""

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

    @staticmethod
    async def get_user_meals_today(
        session: AsyncSession, user_id: uuid.UUID
    ) -> List[Dict[str, Any]]:
        """Retrieves all meals logged today for a user."""
        stmt = select(Meal).where(Meal.user_id == user_id).order_by(Meal.consumed_at.desc())
        result = await session.execute(stmt)
        meals = result.scalars().all()

        return [
            {
                "meal_id": str(m.id),
                "dish_name": m.dish_name,
                "calories": m.calories_est,
                "macros": f"P: {m.proteins_g}g | F: {m.fats_g}g | C: {m.carbs_g}g",
                "time": m.consumed_at.strftime("%H:%M"),
            }
            for m in meals
        ]
