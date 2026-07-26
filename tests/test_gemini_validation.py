import io

import pytest
from PIL import Image
from pydantic import ValidationError

from app.integrations.gemini.client import (
    GeminiClientError,
    GeminiVisionClient,
    MealAnalysisSchema,
)


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_meal_schema_rejects_out_of_range_values():
    with pytest.raises(ValidationError):
        MealAnalysisSchema(
            dish_name="Food",
            ingredients=[],
            calories_est=-1,
            proteins_g=1,
            fats_g=1,
            carbs_g=1,
            confidence_score=1.1,
            coaching_tip="Estimate only",
        )


@pytest.mark.asyncio
async def test_declared_mime_must_match_image_content():
    with pytest.raises(GeminiClientError) as caught:
        await GeminiVisionClient.analyze_food_photo(_png_bytes(), "image/jpeg")
    assert caught.value.error_code == "IMAGE_TYPE_MISMATCH"


@pytest.mark.asyncio
async def test_invalid_image_is_rejected_before_provider_call():
    with pytest.raises(GeminiClientError) as caught:
        await GeminiVisionClient.analyze_food_photo(b"not-an-image", "image/jpeg")
    assert caught.value.error_code == "IMAGE_INVALID"
