import json
from typing import Dict, Any, List
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from app.config.settings import settings


class MealAnalysisSchema(BaseModel):
    dish_name: str = Field(description="Name of the main dish identified in the photo")
    ingredients: List[str] = Field(description="List of detected ingredients")
    calories_est: int = Field(description="Estimated calories in kcal")
    proteins_g: float = Field(description="Estimated proteins in grams")
    fats_g: float = Field(description="Estimated fats in grams")
    carbs_g: float = Field(description="Estimated carbohydrates in grams")
    confidence_score: float = Field(description="Confidence rating from 0.0 to 1.0")
    coaching_tip: str = Field(description="Short nutrition feedback tip")


class GeminiVisionClient:
    """Gemini API Client for multimodal food photo analysis."""

    @classmethod
    async def analyze_food_photo(cls, image_bytes: bytes, mime_type: str = "image/jpeg") -> Dict[str, Any]:
        """Analyzes food image bytes and returns structured nutrition estimations."""
        if not settings.GEMINI_API_KEY:
            # Fallback mock for testing when API key is not yet set up by user
            return {
                "dish_name": "Grilled Chicken Salad with Avocado",
                "ingredients": ["chicken breast", "avocado", "mixed greens", "olive oil"],
                "calories_est": 450,
                "proteins_g": 35.0,
                "fats_g": 22.0,
                "carbs_g": 12.0,
                "confidence_score": 0.88,
                "coaching_tip": "Excellent high-protein lunch with healthy fats!",
            }

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        
        prompt = (
            "Analyze this food photo. Identify the dish, estimate ingredients, portion size, "
            "calories (kcal), and macronutrients (proteins, fats, carbs in grams). "
            "Provide a realistic estimation range rather than false precision."
        )

        response = client.models.generate_content(
            model=settings.GEMINI_VISION_MODEL,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=MealAnalysisSchema,
            ),
        )

        return json.loads(response.text)
