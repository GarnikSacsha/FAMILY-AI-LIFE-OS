import asyncio
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


@pytest.mark.asyncio
async def test_daily_request_does_not_download_history_or_cache_it(archive_db):
    factory, _, user = archive_db
    ranges = []

    async def fetch(name, **kwargs):
        ranges.append((kwargs["start_date"], kwargs["end_date"]))
        if name == "daily_sleep":
            return {"data": [{"day": kwargs["end_date"].isoformat(), "score": 80}]}
        return {"data": []}

    with (
        patch.object(HealthTools, "get_valid_oura_access_token", AsyncMock(return_value="synthetic")),
        patch.object(OuraClient, "get_collection_range", side_effect=fetch),
    ):
        async with factory.begin() as session:
            summary = await get_saved_daily_summary(session, user_id=user)
        assert summary["sleep_score"] == 80
        assert all(start == end for start, end in ranges)
        ranges.clear()
        async with factory.begin() as session:
            await sync_and_load_health(session, user_id=user)
        assert any((end - start).days == 90 for start, end in ranges)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_provider_failure_is_visible_even_with_legacy_archive(archive_db, status, caplog):
    factory, _, user = archive_db
    async with factory.begin() as session:
        session.add(OuraDailyMetric(user_id=user, metric_date=date(2026, 9, 14)))
    with (
        patch.object(HealthTools, "get_valid_oura_access_token", AsyncMock(return_value="synthetic")),
        patch.object(
            OuraClient,
            "get_collection_range",
            AsyncMock(side_effect=OuraOAuthError("PRIVATE_BODY", status_code=status)),
        ),
    ):
        async with factory.begin() as session:
            result = await sync_and_load_health(session, user_id=user, today=date(2026, 9, 15))
    assert str(status) in result["sync_error"]
    assert str(status) in caplog.text
    assert "PRIVATE_BODY" not in caplog.text
    from app.tools.health_history import format_health_history, summarize_history

    report = format_health_history(summarize_history(result, days=7, today=date(2026, 9, 15)))
    assert str(status) in report
    assert "Проверьте синхронизацию кольца" not in report


@pytest.mark.asyncio
async def test_timeout_preserves_completed_collections(archive_db):
    factory, _, user = archive_db
    original_timeout = asyncio.timeout

    async def fetch(name, **kwargs):
        if name == "daily_sleep":
            return {"data": [{"day": "2026-09-14", "score": 80}]}
        await asyncio.Event().wait()

    with (
        patch.object(HealthTools, "get_valid_oura_access_token", AsyncMock(return_value="synthetic")),
        patch.object(OuraClient, "get_collection_range", side_effect=fetch),
        patch("app.tools.health_archive.asyncio.timeout", side_effect=lambda _: original_timeout(0.1)),
    ):
        async with factory.begin() as session:
            result = await sync_and_load_health(session, user_id=user, today=date(2026, 9, 15))
    assert result["summaries"][0]["sleep_score"] == 80
    assert result["partial"]
    assert "не ответила вовремя" in result["sync_error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/health", "/weekly_health", "/monthly_health"])
async def test_command_displays_safe_provider_error(command):
    from app.orchestration.orchestrator import MainOrchestrator

    message = "Oura запретила чтение данных (403)."
    target = "get_saved_daily_summary" if command == "/health" else "get_health_history"
    with patch(
        f"app.orchestration.orchestrator.{target}",
        AsyncMock(side_effect=HealthIntegrationError("PRIVATE_BODY", user_message=message)),
    ):
        response = await MainOrchestrator.process_user_message(
            session=object(),
            user_id=uuid.uuid4(),
            household_id=uuid.uuid4(),
            user_name="Test",
            message_text=command,
        )
    assert message in response
    assert "PRIVATE_BODY" not in response
    assert "синхронизируйте" not in response


@pytest.mark.asyncio
async def test_daily_empty_partial_archive_does_not_blame_ring():
    from app.orchestration.orchestrator import MainOrchestrator

    with patch(
        "app.orchestration.orchestrator.get_saved_daily_summary",
        AsyncMock(return_value={"date": "2026-09-29", "sync_error": "Oura не ответила вовремя."}),
    ):
        response = await MainOrchestrator.process_user_message(
            session=object(),
            user_id=uuid.uuid4(),
            household_id=uuid.uuid4(),
            user_name="Test",
            message_text="/health",
        )
    assert "не ответила вовремя" in response
    assert "синхронизируйте" not in response
