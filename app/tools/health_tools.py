import asyncio
import uuid
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.health.models import Meal
from app.domains.identity.models import OAuthToken
from app.integrations.oura.client import OuraClient, OuraOAuthError
from app.security.token_cipher import TokenCipher, get_token_cipher


class HealthIntegrationError(Exception):
    """Safe health-integration failure without tokens or provider response bodies."""


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
            if scope is not None:
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
    async def get_oura_connection_status(
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Return deterministic per-user connection state without exposing tokens."""
        result = await session.execute(
            select(OAuthToken).where(
                OAuthToken.user_id == user_id,
                OAuthToken.provider == "oura",
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            return {
                "connected": False,
                "expires_at": None,
            }
        expires_at = record.expires_at
        return {
            "connected": True,
            "expires_at": expires_at.isoformat() if expires_at is not None else None,
        }

    @staticmethod
    async def get_valid_oura_access_token(
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        cipher: TokenCipher | None = None,
    ) -> str:
        """Return a valid token, atomically persisting Oura's rotated refresh token."""
        result = await session.execute(
            select(OAuthToken)
            .where(
                OAuthToken.user_id == user_id,
                OAuthToken.provider == "oura",
            )
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise HealthIntegrationError("Oura is not connected.")

        token_cipher = cipher or get_token_cipher()
        expires_at = record.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at is not None and expires_at > datetime.now(timezone.utc) + timedelta(seconds=60):
            try:
                return token_cipher.decrypt(
                    record.access_token_encrypted,
                    user_id=user_id,
                    provider="oura",
                    token_type="access_token",  # noqa: S106  # nosec B106
                )
            except Exception as exc:
                raise HealthIntegrationError("Oura connection must be renewed.") from exc

        if not record.refresh_token_encrypted:
            raise HealthIntegrationError("Oura connection must be renewed.")
        try:
            refresh_token = token_cipher.decrypt(
                record.refresh_token_encrypted,
                user_id=user_id,
                provider="oura",
                token_type="refresh_token",  # noqa: S106  # nosec B106
            )
            tokens = await OuraClient.refresh_access_token(refresh_token)
            if not tokens.get("scope") and record.scope:
                tokens["scope"] = record.scope
            await HealthTools.save_oura_tokens(
                session,
                user_id=user_id,
                tokens=tokens,
                cipher=token_cipher,
            )
        except Exception as exc:
            raise HealthIntegrationError("Oura connection must be renewed.") from exc
        return str(tokens["access_token"])

    @staticmethod
    def _day_record(payload: Mapping[str, Any], target_day: date) -> Mapping[str, Any] | None:
        data = payload.get("data")
        if not isinstance(data, list):
            return None
        target = target_day.isoformat()
        for item in data:
            if isinstance(item, dict) and item.get("day") == target:
                return item
        return None

    @staticmethod
    def _number(value: object) -> int | float | None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        return None

    @staticmethod
    def _main_sleep_record(payload: Mapping[str, Any], target_day: date) -> Mapping[str, Any] | None:
        data = payload.get("data")
        if not isinstance(data, list):
            return None
        target = target_day.isoformat()
        candidates = [item for item in data if isinstance(item, dict) and item.get("day") == target]
        if not candidates:
            return None
        long_sleeps = [item for item in candidates if item.get("type") == "long_sleep"]
        pool = long_sleeps or candidates
        return max(
            pool,
            key=lambda item: HealthTools._number(item.get("total_sleep_duration")) or 0,
        )

    @staticmethod
    def _oura_analysis(summary: Mapping[str, Any]) -> str:
        readiness = HealthTools._number(summary.get("readiness_score"))
        sleep_score = HealthTools._number(summary.get("sleep_score"))
        total_sleep = HealthTools._number(summary.get("total_sleep_seconds"))

        if readiness is not None and readiness < 70:
            return (
                "Восстановление сегодня ниже оптимального. Лучше выбрать лёгкую нагрузку, "
                "добавить прогулку и дать приоритет раннему сну."
            )
        if (sleep_score is not None and sleep_score < 70) or (total_sleep is not None and total_sleep < 7 * 60 * 60):
            return (
                "Сон оказался короче или слабее желаемого. Снизьте интенсивность нагрузки "
                "и постарайтесь лечь спать раньше."
            )
        if readiness is not None and readiness >= 85:
            return (
                "Показатели восстановления хорошие. Обычная нагрузка выглядит уместно, "
                "если самочувствие это подтверждает."
            )
        return (
            "Восстановление выглядит умеренным. Подойдёт обычный день без перегрузки; "
            "ориентируйтесь также на собственное самочувствие."
        )

    @staticmethod
    async def get_oura_daily_summary(
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        timezone_name: str = "Europe/Kyiv",
        day: date | None = None,
    ) -> dict[str, Any]:
        """Fetch a detailed daily health summary from allowlisted Oura V2 endpoints."""
        target_day = day or datetime.now(ZoneInfo(timezone_name)).date()
        access_token = await HealthTools.get_valid_oura_access_token(
            session,
            user_id=user_id,
        )
        collection_names = (
            "daily_sleep",
            "daily_readiness",
            "daily_activity",
            "sleep",
            "daily_spo2",
            "daily_stress",
        )
        results = await asyncio.gather(
            *(
                OuraClient.get_daily_collection(
                    collection,
                    access_token=access_token,
                    start_date=target_day,
                    end_date=target_day,
                )
                for collection in collection_names
            ),
            return_exceptions=True,
        )
        provider_errors = [result for result in results if isinstance(result, OuraOAuthError)]
        if any(error.status_code == 401 for error in provider_errors):
            raise HealthIntegrationError("Oura connection must be renewed.")

        payloads: dict[str, Mapping[str, Any]] = {
            name: result for name, result in zip(collection_names, results, strict=True) if isinstance(result, dict)
        }
        if not any(name in payloads for name in collection_names[:3]):
            first_error = next((result for result in results if isinstance(result, Exception)), None)
            raise HealthIntegrationError("Oura data is temporarily unavailable.") from first_error

        daily_sleep = HealthTools._day_record(payloads.get("daily_sleep", {}), target_day)
        readiness = HealthTools._day_record(payloads.get("daily_readiness", {}), target_day)
        activity = HealthTools._day_record(payloads.get("daily_activity", {}), target_day)
        detailed_sleep = HealthTools._main_sleep_record(payloads.get("sleep", {}), target_day)
        spo2 = HealthTools._day_record(payloads.get("daily_spo2", {}), target_day)
        stress = HealthTools._day_record(payloads.get("daily_stress", {}), target_day)

        readiness_contributors: Mapping[str, Any] = {}
        if isinstance(readiness, Mapping):
            contributors = readiness.get("contributors")
            if isinstance(contributors, Mapping):
                readiness_contributors = contributors

        spo2_percentage: Mapping[str, Any] = {}
        if isinstance(spo2, Mapping):
            percentage = spo2.get("spo2_percentage")
            if isinstance(percentage, Mapping):
                spo2_percentage = percentage
        summary = {
            "date": target_day.isoformat(),
            "sleep_score": HealthTools._number(daily_sleep.get("score")) if daily_sleep else None,
            "readiness_score": HealthTools._number(readiness.get("score")) if readiness else None,
            "activity_score": HealthTools._number(activity.get("score")) if activity else None,
            "total_sleep_seconds": (
                HealthTools._number(detailed_sleep.get("total_sleep_duration")) if detailed_sleep else None
            ),
            "deep_sleep_seconds": (
                HealthTools._number(detailed_sleep.get("deep_sleep_duration")) if detailed_sleep else None
            ),
            "rem_sleep_seconds": (
                HealthTools._number(detailed_sleep.get("rem_sleep_duration")) if detailed_sleep else None
            ),
            "awake_seconds": HealthTools._number(detailed_sleep.get("awake_time")) if detailed_sleep else None,
            "sleep_efficiency": (HealthTools._number(detailed_sleep.get("efficiency")) if detailed_sleep else None),
            "average_hrv_ms": (HealthTools._number(detailed_sleep.get("average_hrv")) if detailed_sleep else None),
            "lowest_heart_rate_bpm": (
                HealthTools._number(detailed_sleep.get("lowest_heart_rate")) if detailed_sleep else None
            ),
            "temperature_deviation_c": (
                HealthTools._number(readiness.get("temperature_deviation")) if readiness else None
            ),
            "recovery_index": HealthTools._number(readiness_contributors.get("recovery_index")),
            "steps": HealthTools._number(activity.get("steps")) if activity else None,
            "active_calories": HealthTools._number(activity.get("active_calories")) if activity else None,
            "total_calories": HealthTools._number(activity.get("total_calories")) if activity else None,
            "high_activity_seconds": (HealthTools._number(activity.get("high_activity_time")) if activity else None),
            "medium_activity_seconds": (
                HealthTools._number(activity.get("medium_activity_time")) if activity else None
            ),
            "spo2_average_percent": HealthTools._number(spo2_percentage.get("average")),
            "breathing_disturbance_index": (
                HealthTools._number(spo2.get("breathing_disturbance_index")) if spo2 else None
            ),
            "stress_summary": stress.get("day_summary") if stress else None,
            "stress_high": HealthTools._number(stress.get("stress_high")) if stress else None,
            "recovery_high": HealthTools._number(stress.get("recovery_high")) if stress else None,
        }
        summary["analysis"] = HealthTools._oura_analysis(summary)
        return summary

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
