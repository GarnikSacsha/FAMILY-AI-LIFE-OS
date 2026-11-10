import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.application import create_application
from app.domains.identity.service import ActorContext, PermissionDeniedError
from app.infrastructure.integrations import google_sheets_worker as worker
from app.integrations.google.sheets import GoogleSheetsClient
from app.tools.google_tools import GoogleWorkspaceError, GoogleWorkspaceTools


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["claim", "mark_synced", "mark_failed"])
async def test_sheets_worker_recovers_after_database_failure(monkeypatch, failure_stage):
    item = worker.TransactionSyncItem(
        id=uuid.uuid4(),
        occurred_at=datetime.now(timezone.utc),
        merchant="Market",
        category="Groceries",
        amount="10.00",
        currency="UAH",
        direction="expense",
        source="manual",
        owner_type="household",
    )
    claim = AsyncMock(
        side_effect=[RuntimeError("database unavailable"), item, asyncio.CancelledError()]
        if failure_stage == "claim"
        else [item, item, asyncio.CancelledError()]
    )
    append = AsyncMock(return_value="A2:I2")
    synced = AsyncMock(
        side_effect=[RuntimeError("database unavailable"), None] if failure_stage == "mark_synced" else None
    )
    failed = AsyncMock(side_effect=RuntimeError("database unavailable"))
    if failure_stage == "mark_failed":
        append.side_effect = [RuntimeError("provider unavailable"), "A2:I2"]
    monkeypatch.setattr(GoogleSheetsClient, "is_configured", lambda: True)
    monkeypatch.setattr(GoogleSheetsClient, "append_transaction", append)
    monkeypatch.setattr(worker, "_claim_next_transaction", claim)
    monkeypatch.setattr(worker, "_mark_synced", synced)
    monkeypatch.setattr(worker, "_mark_failed", failed)
    sleep = AsyncMock()
    monkeypatch.setattr(worker.asyncio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        await worker.run_google_sheets_worker()

    assert claim.await_count == 3
    assert synced.await_count >= 1
    sleep.assert_awaited()


@pytest.mark.asyncio
async def test_health_detects_stopped_sheets_worker(monkeypatch):
    application = create_application(start_telegram=False)
    application.state.google_sheets_task = SimpleNamespace(done=lambda: True)
    monkeypatch.setattr("app.api.application._database_is_ready", AsyncMock(return_value=True))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.get("/health")
        live = await client.get("/live")
    assert response.status_code == 503
    assert response.json()["google_sheets_worker"] == "stopped"
    assert live.status_code == 200


def test_compose_initializes_family_on_fresh_database():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    assert compose["services"]["migrate"]["command"] == ["python", "-m", "app.infrastructure.database.prepare"]


@pytest.mark.asyncio
async def test_calendar_tool_checks_actual_actor_before_provider_calls(monkeypatch):
    actor = ActorContext(
        user_id=uuid.uuid4(), household_id=uuid.uuid4(), telegram_id=1001, chat_id=-100, chat_type="supergroup"
    )
    provider = AsyncMock(return_value={"id": "event"})
    scope = AsyncMock()
    monkeypatch.setattr(GoogleWorkspaceTools, "_require_calendar_scope", scope)
    monkeypatch.setattr(GoogleWorkspaceTools, "get_valid_access_token", AsyncMock(return_value="test-token"))
    monkeypatch.setattr(GoogleWorkspaceTools, "_calendar_request", provider)
    async with AsyncSession(info={"actor_context": actor}) as session:
        start = datetime(2027, 1, 1, 10, tzinfo=timezone.utc)
        with pytest.raises(PermissionDeniedError):
            await GoogleWorkspaceTools.create_calendar_event(
                session,
                user_id=actor.user_id,
                summary="Meeting",
                start_at=start,
                end_at=start + timedelta(hours=1),
                timezone_name="Europe/Kyiv",
            )
    provider.assert_not_awaited()
    scope.assert_not_awaited()


@pytest.mark.asyncio
async def test_calendar_retry_recovers_event_after_lost_response(monkeypatch):
    stored = {}
    calls = []

    async def request(method, url, *, access_token, json_body=None):
        calls.append(method)
        if method == "GET":
            return stored[url.rsplit("/", 1)[-1]]
        event_id = json_body.get("id", str(uuid.uuid4()))
        if event_id in stored:
            raise GoogleWorkspaceError("conflict", error_code="GOOGLE_HTTP_409")
        stored[event_id] = {**json_body, "id": event_id}
        raise GoogleWorkspaceError("response lost", error_code="GOOGLE_CALENDAR_UNAVAILABLE")

    monkeypatch.setattr(GoogleWorkspaceTools, "_require_calendar_scope", AsyncMock())
    monkeypatch.setattr(GoogleWorkspaceTools, "get_valid_access_token", AsyncMock(return_value="test-token"))
    monkeypatch.setattr(GoogleWorkspaceTools, "_calendar_request", request)
    user_id = uuid.uuid4()
    start = datetime(2027, 1, 1, 10, tzinfo=timezone.utc)
    async with AsyncSession(info={"telegram_request_key": "telegram:1001:50"}) as session:
        kwargs = dict(
            user_id=user_id,
            summary="Meeting",
            start_at=start,
            end_at=start + timedelta(hours=1),
            timezone_name="Europe/Kyiv",
        )
        first = await GoogleWorkspaceTools.create_calendar_event(session, **kwargs)
        second = await GoogleWorkspaceTools.create_calendar_event(session, **kwargs)
    assert first == second
    assert len(stored) == 1
    assert calls == ["POST", "GET", "POST", "GET"]


@pytest.mark.asyncio
async def test_calendar_distinct_messages_still_create_distinct_events(monkeypatch):
    async def request(method, url, *, access_token, json_body=None):
        return json_body

    monkeypatch.setattr(GoogleWorkspaceTools, "_require_calendar_scope", AsyncMock())
    monkeypatch.setattr(GoogleWorkspaceTools, "get_valid_access_token", AsyncMock(return_value="test-token"))
    monkeypatch.setattr(GoogleWorkspaceTools, "_calendar_request", request)
    user_id = uuid.uuid4()
    start = datetime(2027, 1, 1, 10, tzinfo=timezone.utc)
    async with AsyncSession(info={"telegram_request_key": "telegram:1001:50"}) as session:
        kwargs = dict(
            user_id=user_id,
            summary="Meeting",
            start_at=start,
            end_at=start + timedelta(hours=1),
            timezone_name="Europe/Kyiv",
        )
        first = await GoogleWorkspaceTools.create_calendar_event(session, **kwargs)
        session.info["telegram_request_key"] = "telegram:1001:51"
        second = await GoogleWorkspaceTools.create_calendar_event(session, **kwargs)
    assert first["id"] != second["id"]
    assert first["summary"] == second["summary"] == "Meeting"


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery", ["missing", "cancelled", "wrong_id"])
async def test_calendar_does_not_report_success_for_unverified_recovery(monkeypatch, recovery):
    async def request(method, url, *, access_token, json_body=None):
        if method == "POST":
            raise GoogleWorkspaceError("response lost", error_code="GOOGLE_CALENDAR_UNAVAILABLE")
        if recovery == "missing":
            raise GoogleWorkspaceError("missing", error_code="GOOGLE_CALENDAR_EVENT_NOT_FOUND")
        return {
            "id": "wrong" if recovery == "wrong_id" else url.rsplit("/", 1)[-1],
            "status": "cancelled" if recovery == "cancelled" else "confirmed",
        }

    monkeypatch.setattr(GoogleWorkspaceTools, "_require_calendar_scope", AsyncMock())
    monkeypatch.setattr(GoogleWorkspaceTools, "get_valid_access_token", AsyncMock(return_value="test-token"))
    monkeypatch.setattr(GoogleWorkspaceTools, "_calendar_request", request)
    start = datetime(2027, 1, 1, 10, tzinfo=timezone.utc)
    async with AsyncSession(info={"telegram_request_key": "telegram:1001:50"}) as session:
        with pytest.raises(GoogleWorkspaceError) as caught:
            await GoogleWorkspaceTools.create_calendar_event(
                session,
                user_id=uuid.uuid4(),
                summary="Meeting",
                start_at=start,
                end_at=start + timedelta(hours=1),
                timezone_name="Europe/Kyiv",
            )
    assert caught.value.error_code == "GOOGLE_CALENDAR_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type", ["private", "supergroup"])
async def test_semantic_calendar_obeys_authenticated_chat_boundary(monkeypatch, chat_type):
    from contextlib import asynccontextmanager

    from app.domains.planning.semantic_calendar import SemanticCalendarPlan
    from app.telegram import bot as telegram

    actor = ActorContext(
        user_id=uuid.uuid4(),
        household_id=uuid.uuid4(),
        telegram_id=1001,
        chat_id=1001 if chat_type == "private" else -100,
        chat_type=chat_type,
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=actor.telegram_id, first_name="Test"),
        chat=SimpleNamespace(id=actor.chat_id, type=chat_type),
        message_id=52,
        text="Schedule an interview at 10:00",
        caption=None,
        photo=None,
        document=None,
        answer=AsyncMock(),
        voice=None,
        audio=None,
    )

    @asynccontextmanager
    async def uow():
        async with AsyncSession() as session:
            yield session

    monkeypatch.setattr(telegram, "unit_of_work", uow)
    monkeypatch.setattr(telegram.IdentityService, "resolve_actor", AsyncMock(return_value=actor))
    monkeypatch.setattr(telegram.SharedMemoryTools, "record_message", AsyncMock())
    monkeypatch.setattr(telegram.SharedMemoryTools, "get_pending_action", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "app.orchestration.orchestrator.CalendarIntentInterpreter.interpret",
        AsyncMock(
            return_value=SemanticCalendarPlan(
                intent="calendar", title="Interview", event_date="2027-01-01", event_time="10:00", confidence=1
            ),
        ),
    )
    monkeypatch.setattr(GoogleWorkspaceTools, "_require_calendar_scope", AsyncMock())
    monkeypatch.setattr(GoogleWorkspaceTools, "get_valid_access_token", AsyncMock(return_value="test-token"))
    provider = AsyncMock(return_value={"id": "event", "summary": "Interview"})
    monkeypatch.setattr(GoogleWorkspaceTools, "_calendar_request", provider)
    await telegram.handle_user_message(message)
    if chat_type == "private":
        provider.assert_awaited_once()
        assert "Interview" in message.answer.call_args.args[0]
    else:
        provider.assert_not_awaited()
        assert "Доступ запрещён" in message.answer.call_args.args[0]
