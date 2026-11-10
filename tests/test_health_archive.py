import base64
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.domains.health.models import HealthProviderConnection, OuraDailyMetric
from app.domains.identity.service import ActorContext, PermissionDeniedError
from app.integrations.oura.client import OuraClient, OuraOAuthError
from app.security.token_cipher import TokenCipher, TokenCipherError
from app.tools.health_archive import _decode, get_saved_daily_summary, sync_and_load_health
from app.tools.health_insights import daily_insight, period_insight
from app.tools.health_tools import HealthIntegrationError, HealthTools
from tests.test_shared_memory import _memory_database, _seed_family


@pytest_asyncio.fixture
async def archive_db():
    engine, factory = await _memory_database()
    async with engine.begin() as conn:
        await conn.run_sync(HealthProviderConnection.__table__.create)
        await conn.run_sync(OuraDailyMetric.__table__.create)
    household, user = await _seed_family(factory)
    cipher = TokenCipher(base64.b64encode(b"h" * 32).decode())
    with patch("app.tools.health_archive.get_token_cipher", return_value=cipher):
        yield factory, household, user
    await engine.dispose()


def provider(score=80):
    async def fetch(name, **kwargs):
        if name == "daily_sleep":
            return {"data": [{"day": "2026-09-14", "score": score}]}
        if name == "sleep":
            return {
                "data": [
                    {
                        "day": "2026-09-14",
                        "type": "long_sleep",
                        "average_hrv": 45,
                        "average_breath": 15,
                        "latency": 600,
                        "total_sleep_duration": 28000,
                    }
                ]
            }
        return {"data": []}

    return fetch


@pytest.mark.asyncio
async def test_archive_survives_sessions_updates_without_duplicates_and_keeps_data_on_failure(archive_db):
    factory, _, user = archive_db
    with (
        patch.object(HealthTools, "get_valid_oura_access_token", AsyncMock(return_value="synthetic")),
        patch.object(OuraClient, "get_collection_range", new=provider()),
    ):
        async with factory.begin() as session:
            result = await sync_and_load_health(session, user_id=user, today=date(2026, 9, 15))
        assert result["summaries"][0]["sleep_score"] == 80
    async with factory.begin() as session:
        row = (await session.execute(select(OuraDailyMetric))).scalar_one()
        assert "average_hrv" not in str(row.raw_json)
        assert _decode(row)["summary"]["average_breath_per_minute"] == 15
        state = (await session.execute(select(HealthProviderConnection))).scalar_one()
        state.last_sync_succeeded_at = datetime.now(timezone.utc) - timedelta(hours=1)
    with (
        patch.object(HealthTools, "get_valid_oura_access_token", AsyncMock(return_value="synthetic")),
        patch.object(OuraClient, "get_collection_range", new=provider(90)),
    ):
        async with factory.begin() as session:
            result = await sync_and_load_health(session, user_id=user, today=date(2026, 9, 15))
        assert result["summaries"][0]["sleep_score"] == 90
    async with factory.begin() as session:
        assert len(list((await session.execute(select(OuraDailyMetric))).scalars())) == 1
        state = (await session.execute(select(HealthProviderConnection))).scalar_one()
        state.last_sync_succeeded_at = datetime.now(timezone.utc) - timedelta(hours=1)
    with patch.object(
        HealthTools, "get_valid_oura_access_token", AsyncMock(side_effect=HealthIntegrationError("offline"))
    ):
        async with factory.begin() as session:
            fallback = await sync_and_load_health(session, user_id=user, today=date(2026, 9, 15))
        assert fallback["partial"] and fallback["summaries"][0]["sleep_score"] == 90


@pytest.mark.asyncio
async def test_archive_actor_scope_and_encryption_are_user_bound(archive_db):
    factory, household, user = archive_db
    with (
        patch.object(HealthTools, "get_valid_oura_access_token", AsyncMock(return_value="synthetic")),
        patch.object(OuraClient, "get_collection_range", new=provider()),
    ):
        async with factory.begin() as session:
            await sync_and_load_health(session, user_id=user, today=date(2026, 9, 15))
    async with factory() as session:
        row = (await session.execute(select(OuraDailyMetric))).scalar_one()
        row.user_id = uuid.uuid4()
        with pytest.raises(TokenCipherError):
            _decode(row)
    async with factory() as session:
        session.info["actor_context"] = ActorContext(
            user_id=user, household_id=household, telegram_id=123, chat_id=-100, chat_type="group"
        )
        with pytest.raises(PermissionDeniedError):
            await sync_and_load_health(session, user_id=user)


@pytest.mark.asyncio
async def test_no_provider_and_no_archive_is_not_empty_success(archive_db):
    factory, _, user = archive_db
    with patch.object(
        HealthTools, "get_valid_oura_access_token", AsyncMock(side_effect=HealthIntegrationError("offline"))
    ):
        async with factory.begin() as session:
            with pytest.raises(HealthIntegrationError):
                await sync_and_load_health(session, user_id=user)


@pytest.mark.asyncio
async def test_cache_avoids_repeated_provider_calls(archive_db):
    factory, _, user = archive_db
    with (
        patch.object(HealthTools, "get_valid_oura_access_token", AsyncMock(return_value="synthetic")),
        patch.object(OuraClient, "get_collection_range", side_effect=provider()) as fetch,
    ):
        async with factory.begin() as session:
            await sync_and_load_health(session, user_id=user, today=date(2026, 9, 15))
        async with factory.begin() as session:
            await sync_and_load_health(session, user_id=user, today=date(2026, 9, 15))
        assert fetch.call_count == 7


@pytest.mark.asyncio
async def test_partial_response_preserves_existing_collection(archive_db):
    factory, _, user = archive_db
    with (
        patch.object(HealthTools, "get_valid_oura_access_token", AsyncMock(return_value="synthetic")),
        patch.object(OuraClient, "get_collection_range", side_effect=provider()),
    ):
        async with factory.begin() as session:
            await sync_and_load_health(session, user_id=user, today=date(2026, 9, 15))
    async with factory.begin() as session:
        state = (await session.execute(select(HealthProviderConnection))).scalar_one()
        state.last_sync_succeeded_at = datetime.now(timezone.utc) - timedelta(hours=1)

    async def partial(name, **kwargs):
        if name == "sleep":
            raise OuraOAuthError("unavailable", status_code=403)
        return await provider(90)(name, **kwargs)

    with (
        patch.object(HealthTools, "get_valid_oura_access_token", AsyncMock(return_value="synthetic")),
        patch.object(OuraClient, "get_collection_range", side_effect=partial),
    ):
        async with factory.begin() as session:
            result = await sync_and_load_health(session, user_id=user, today=date(2026, 9, 15))
    assert result["partial"]
    assert result["summaries"][0]["sleep_score"] == 90
    assert result["summaries"][0]["average_hrv_ms"] == 45


def test_insight_requires_three_consecutive_days_and_personal_baseline():
    today = date(2026, 9, 15)
    rows = [
        {"date": (today - timedelta(days=i)).isoformat(), "lowest_heart_rate_bpm": 65 if i < 3 else 55}
        for i in range(33)
    ]
    assert "3 дня подряд" in daily_insight(rows[0], rows)
    rows[1]["lowest_heart_rate_bpm"] = None
    assert "3 дня подряд" not in daily_insight(rows[0], rows)
    assert "истории пока мало" in daily_insight({"date": today.isoformat(), "sleep_score": 80}, [])


def test_period_insight_does_not_claim_trend_from_one_reading():
    assert "мало данных" in period_insight(
        {
            "days": 7,
            "metrics": {"readiness_score": {"current": {"mean": 60, "count": 1}, "previous": {"mean": 90, "count": 7}}},
        }
    )


@pytest.mark.asyncio
async def test_today_does_not_substitute_yesterday():
    archive = {
        "today": "2026-09-15",
        "summaries": [{"date": "2026-09-14", "sleep_score": 80}],
        "partial": True,
        "last_sync": "2026-09-14T10:00:00+00:00",
    }
    with patch("app.tools.health_archive.sync_and_load_health", AsyncMock(return_value=archive)):
        result = await get_saved_daily_summary(object(), user_id=uuid.uuid4())
    assert result["date"] == "2026-09-15" and result.get("sleep_score") is None
