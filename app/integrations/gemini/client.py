import json
from typing import Dict, Any, List
from pydantic import BaseModel, Field, field_validator
from google import genai
from google.genai import types
from app.config.settings import settings


class GeminiClientError(Exception):
    """Exception raised for Gemini API call failures."""
    pass


class MealAnalysisSchema(BaseModel):
    dish_name: str = Field(description="Name of the main dish identified in the photo")
    ingredients: List[str] = Field(description="List of detected ingredients")
    calories_est: int = Field(description="Estimated calories in kcal")
    proteins_g: float = Field(description="Estimated proteins in grams")
    fats_g: float = Field(description="Estimated fats in grams")
    carbs_g: float = Field(description="Estimated carbohydrates in grams")
    confidence_score: float = Field(description="Confidence rating from 0.0 to 1.0")
    coaching_tip: str = Field(description="Short nutrition feedback tip")

    @field_validator("calories_est", mode="after")
    @classmethod
    def validate_calories(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Calories cannot be negative.")
        return v

    @field_validator("proteins_g", "fats_g", "carbs_g", mode="after")
    @classmethod
    def validate_macros(cls, v: float) -> float:
        if v < 0.0:
            raise ValueError("Macro values cannot be negative.")
        return round(v, 1)

    @field_validator("confidence_score", mode="after")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


class GeminiVisionClient:
    """Gemini API Client for multimodal food photo analysis."""

    MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB limit

    @classmethod
    async def analyze_food_photo(cls, image_bytes: bytes, mime_type: str = "image/jpeg") -> Dict[str, Any]:
        """Analyzes food image bytes and returns validated nutrition estimations."""
        if not settings.GEMINI_API_KEY:
            raise GeminiClientError(
                "GEMINI_API_KEY is not configured on the server. Food photo analysis is temporarily unavailable."
            )

        if len(image_bytes) > cls.MAX_IMAGE_SIZE_BYTES:
            raise GeminiClientError(
                f"Image file size ({len(image_bytes)} bytes) exceeds maximum limit of 10MB."
            )

        client = genai.Client(api_key=settings.GEMINI_API_KEY.get_secret_value() if hasattr(settings.GEMINI_API_KEY, "get_secret_value") else str(settings.GEMINI_API_KEY))
        
        prompt = (
            "Analyze this food photo. Identify the dish, estimate ingredients, portion size, "
            "calories (kcal), and macronutrients (proteins, fats, carbs in grams). "
            "Provide a realistic estimation range rather than false precision."
        )

        try:
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
            raw_data = json.loads(response.text)
            validated = MealAnalysisSchema(**raw_data)
            return validated.model_dump()
        except Exception as e:
            raise GeminiClientError(f"Gemini Vision API analysis failed: {e}")
