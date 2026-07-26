import asyncio
import io
import logging
import warnings
from importlib import import_module
from typing import Annotated, Any

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.config.settings import settings

genai: Any = None
types: Any = None
try:
    genai = import_module("google.genai")
    types = import_module("google.genai.types")
except ImportError:  # pragma: no cover - exercised only in incomplete deployments
    pass


logger = logging.getLogger(__name__)
Ingredient = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class GeminiClientError(Exception):
    """Safe, user-displayable Gemini failure with a stable internal error code."""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class MealAnalysisSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dish_name: str = Field(min_length=1, max_length=200)
    ingredients: list[Ingredient] = Field(default_factory=list, max_length=50)
    calories_est: int = Field(ge=0, le=10_000)
    proteins_g: float = Field(ge=0, le=1_000)
    fats_g: float = Field(ge=0, le=1_000)
    carbs_g: float = Field(ge=0, le=1_000)
    confidence_score: float = Field(ge=0, le=1)
    coaching_tip: str = Field(min_length=1, max_length=500)


def _secret_value(value: Any) -> str:
    if value is None:
        return ""
    get_secret_value = getattr(value, "get_secret_value", None)
    return get_secret_value() if callable(get_secret_value) else str(value)


class GeminiVisionClient:
    """Async Gemini food-image analysis with strict upload and response validation."""

    MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024
    MAX_IMAGE_PIXELS = 25_000_000
    ALLOWED_MIME_TYPES = {
        "image/jpeg": "JPEG",
        "image/png": "PNG",
        "image/webp": "WEBP",
    }

    @classmethod
    def _verify_image(cls, image_bytes: bytes, mime_type: str) -> None:
        if not image_bytes:
            raise GeminiClientError("The uploaded image is empty.", error_code="IMAGE_EMPTY")
        if len(image_bytes) > cls.MAX_IMAGE_SIZE_BYTES:
            raise GeminiClientError(
                "The image exceeds the 10 MB upload limit.",
                error_code="IMAGE_TOO_LARGE",
            )
        expected_format = cls.ALLOWED_MIME_TYPES.get(mime_type.lower())
        if expected_format is None:
            raise GeminiClientError(
                "Only JPEG, PNG, and WebP images are supported.",
                error_code="IMAGE_TYPE_UNSUPPORTED",
            )

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(image_bytes)) as image:
                    if image.format != expected_format:
                        raise GeminiClientError(
                            "The image content does not match its declared type.",
                            error_code="IMAGE_TYPE_MISMATCH",
                        )
                    width, height = image.size
                    if width <= 0 or height <= 0 or width * height > cls.MAX_IMAGE_PIXELS:
                        raise GeminiClientError(
                            "The image dimensions exceed the supported limit.",
                            error_code="IMAGE_DIMENSIONS_INVALID",
                        )
                    image.verify()
        except GeminiClientError:
            raise
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
            raise GeminiClientError(
                "The uploaded file is not a valid image.",
                error_code="IMAGE_INVALID",
            ) from exc

    @classmethod
    async def analyze_food_photo(
        cls,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
    ) -> dict[str, Any]:
        await asyncio.to_thread(cls._verify_image, image_bytes, mime_type)

        api_key = _secret_value(getattr(settings, "GEMINI_API_KEY", None)).strip()
        if not api_key:
            raise GeminiClientError(
                "Food photo analysis is temporarily unavailable.",
                error_code="GEMINI_NOT_CONFIGURED",
            )
        if genai is None or types is None:
            logger.error(
                "Gemini SDK is not installed",
                extra={"error_code": "GEMINI_SDK_UNAVAILABLE"},
            )
            raise GeminiClientError(
                "Food photo analysis is temporarily unavailable.",
                error_code="GEMINI_SDK_UNAVAILABLE",
            )

        prompt = (
            "Analyze this food photo. Identify the dish and likely ingredients, then estimate "
            "calories and macronutrients in grams. Treat every value as an estimate, avoid medical "
            "claims, and return only the requested JSON schema."
        )
        timeout_seconds = float(getattr(settings, "GEMINI_TIMEOUT_SECONDS", 15.0))
        client: Any = None
        async_client: Any = None
        try:
            client = genai.Client(api_key=api_key)
            async_client = client.aio
            response = await asyncio.wait_for(
                async_client.models.generate_content(
                    model=settings.GEMINI_VISION_MODEL,
                    contents=[
                        types.Part.from_bytes(data=image_bytes, mime_type=mime_type.lower()),
                        prompt,
                    ],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=MealAnalysisSchema,
                    ),
                ),
                timeout=timeout_seconds,
            )

            parsed = getattr(response, "parsed", None)
            if isinstance(parsed, MealAnalysisSchema):
                validated = parsed
            elif parsed is not None:
                validated = MealAnalysisSchema.model_validate(parsed)
            else:
                response_text = getattr(response, "text", None)
                if not response_text:
                    raise GeminiClientError(
                        "The nutrition service returned an empty response.",
                        error_code="GEMINI_EMPTY_RESPONSE",
                    )
                validated = MealAnalysisSchema.model_validate_json(response_text)
            return validated.model_dump()
        except GeminiClientError:
            raise
        except TimeoutError as exc:
            logger.warning("Gemini request timed out", extra={"error_code": "GEMINI_TIMEOUT"})
            raise GeminiClientError(
                "Food photo analysis timed out. Please try again.",
                error_code="GEMINI_TIMEOUT",
            ) from exc
        except ValidationError as exc:
            logger.warning(
                "Gemini returned an invalid structured response",
                extra={"error_code": "GEMINI_RESPONSE_INVALID"},
            )
            raise GeminiClientError(
                "The nutrition service returned an invalid result.",
                error_code="GEMINI_RESPONSE_INVALID",
            ) from exc
        except Exception as exc:
            # Do not log provider exception text: SDK errors can contain request data.
            logger.warning(
                "Gemini request failed (%s)",
                type(exc).__name__,
                extra={"error_code": "GEMINI_PROVIDER_FAILURE"},
            )
            raise GeminiClientError(
                "Food photo analysis is temporarily unavailable.",
                error_code="GEMINI_PROVIDER_FAILURE",
            ) from exc
        finally:
            close = getattr(async_client, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:
                    logger.warning(
                        "Gemini async client cleanup failed",
                        extra={"error_code": "GEMINI_CLIENT_CLOSE_FAILURE"},
                    )
