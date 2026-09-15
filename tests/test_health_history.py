import uuid
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.integrations.oura.client import OuraClient, OuraOAuthError
from app.orchestration.orchestrator import MainOrchestrator
from app.tools.health_history import format_health_history, get_health_history
from app.tools.health_tools import HealthIntegrationError, HealthTools
from tests.test_health_archive import archive_db  # noqa: F401


@pytest_asyncio.fixture
async def history_session(archive_db):  # noqa: F811
    factory, _, user = archive_db
    async with factory.begin() as session:
        yield session, user


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [7, 30])
async def test_history_missing_days_duplicates_zero_and_today(days, history_session):
    async def collection(name, **kwargs):
        if name == "daily_sleep":
            return {
                "data": [
                    {"day": "2026-09-14", "score": 80},
                    {"day": "2026-09-14", "score": 80},
                    {"day": "2026-09-15", "score": 100},
                    {"day": "2026-09-13", "score": None},
                ]
            }
        if name == "daily_activity":
            return {"data": [{"day": "2026-09-14", "steps": 0}]}
        return {"data": []}

    session, user = history_session
    with (
        patch.object(HealthTools, "get_valid_oura_access_token", AsyncMock(return_value="synthetic")) as token,
        patch.object(OuraClient, "get_collection_range", new=collection),
    ):
        result = await get_health_history(session, user_id=user, days=days, today=date(2026, 9, 15))
    token.assert_awaited_once_with(session, user_id=user)
    assert result["metrics"]["sleep_score"]["current"] == {"mean": 80, "count": 1}
    assert result["metrics"]["steps"]["current"] == {"mean": 0, "count": 1}
    assert result["metrics"]["readiness_score"]["current"] == {"mean": None, "count": 0}
    assert "Сегодняшний" in format_health_history(result)


@pytest.mark.asyncio
async def test_history_comparison_and_main_sleep_selection(history_session):
    session, user = history_session

    async def collection(name, **kwargs):
        if name == "sleep":
            return {
                "data": [
                    {"day": "2026-09-14", "type": "long_sleep", "total_sleep_duration": 28800},
                    {"day": "2026-09-14", "type": "nap", "total_sleep_duration": 3600},
                    {"day": "2026-09-07", "type": "long_sleep", "total_sleep_duration": 25200},
                ]
            }
        return {"data": []}

    with (
        patch.object(HealthTools, "get_valid_oura_access_token", AsyncMock(return_value="synthetic")),
        patch.object(OuraClient, "get_collection_range", new=collection),
    ):
        result = await get_health_history(session, user_id=user, days=7, today=date(2026, 9, 15))
    metric = result["metrics"]["total_sleep_seconds"]
    assert metric["current"]["mean"] == 480
    assert metric["previous"]["mean"] == 420
    assert "+60.0 мин" in format_health_history(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 503])
async def test_history_provider_failure_is_not_empty_success(status, history_session):
    session, user = history_session
    with (
        patch.object(HealthTools, "get_valid_oura_access_token", AsyncMock(return_value="synthetic")),
        patch.object(
            OuraClient, "get_collection_range", AsyncMock(side_effect=OuraOAuthError("unavailable", status_code=status))
        ),
    ):
        with pytest.raises(HealthIntegrationError):
            await get_health_history(session, user_id=user, days=7)


@pytest.mark.asyncio
async def test_history_partial_provider_failure_is_labelled(history_session):
    session, user = history_session

    async def collection(name, **kwargs):
        if name == "sleep":
            raise OuraOAuthError("unavailable", status_code=403)
        return {"data": []}

    with (
        patch.object(HealthTools, "get_valid_oura_access_token", AsyncMock(return_value="synthetic")),
        patch.object(OuraClient, "get_collection_range", new=collection),
    ):
        result = await get_health_history(session, user_id=user, days=7)
    assert result["partial"]
    assert "Сводка неполная" in format_health_history(result)


@pytest.mark.asyncio
async def test_oura_range_loads_all_pages_and_rejects_token_loop():
    with patch.object(
        OuraClient,
        "get_daily_collection",
        AsyncMock(
            side_effect=[
                {"data": [1], "next_token": "page2"},
                {"data": [2], "next_token": None},
            ]
        ),
    ) as get:
        result = await OuraClient.get_collection_range(
            "daily_sleep", access_token="synthetic", start_date=date(2026, 8, 1), end_date=date(2026, 9, 1)
        )
        assert result["data"] == [1, 2]
        assert get.call_args.kwargs["next_token"] == "page2"
    with patch.object(OuraClient, "get_daily_collection", AsyncMock(return_value={"data": [], "next_token": "loop"})):
        with pytest.raises(OuraOAuthError):
            await OuraClient.get_collection_range(
                "daily_sleep", access_token="synthetic", start_date=date(2026, 8, 1), end_date=date(2026, 9, 1)
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,days",
    [("/health week", 7), ("/health month", 30), ("/health 7", 7), ("/weekly_health", 7), ("/monthly_health", 30)],
)
async def test_period_command_routes_to_history(command, days):
    with (
        patch("app.orchestration.orchestrator.get_health_history", AsyncMock(return_value={})) as history,
        patch("app.orchestration.orchestrator.format_health_history", return_value="History"),
    ):
        response = await MainOrchestrator.process_user_message(
            session=object(), user_id=uuid.uuid4(), household_id=uuid.uuid4(), user_name="Test", message_text=command
        )
    assert response == "History"
    assert history.call_args.kwargs["days"] == days
