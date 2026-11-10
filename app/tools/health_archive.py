"""Per-user daily Oura archive using the existing schema and encryption key."""

import asyncio
import json
import logging
import math
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.health.models import HealthProviderConnection, OuraDailyMetric
from app.domains.identity.models import User
from app.domains.identity.service import IdentityService, PermissionDeniedError
from app.integrations.oura.client import OuraClient, OuraOAuthError
from app.security.token_cipher import get_token_cipher
from app.tools.health_tools import HealthIntegrationError, HealthTools

COLLECTIONS = (
    "daily_sleep",
    "daily_readiness",
    "daily_activity",
    "sleep",
    "daily_spo2",
    "daily_stress",
    "daily_resilience",
)
ARCHIVE_DAYS = 90
logger = logging.getLogger(__name__)


def _failure_message(error: BaseException, collection: str) -> str:
    """Only classify errors; never log tokens, URLs, bodies, or exception text."""
    cause = error.__cause__ if isinstance(error, HealthIntegrationError) and error.__cause__ else error
    status = cause.status_code if isinstance(cause, OuraOAuthError) else None
    timed_out = isinstance(cause, TimeoutError) or isinstance(cause.__cause__, TimeoutError)
    category = "timeout" if timed_out else "unavailable"
    logger.warning("Oura read failed collection=%s status=%s category=%s", collection, status, category)
    if collection == "daily_resilience" and status in {401, 403}:
        return (
            f"Oura не разрешила чтение дополнительного показателя устойчивости к стрессу ({status}). "
            "Это не означает, что всё подключение потеряно; остальные показатели загружены, если доступны."
        )
    if status == 401:
        return "Oura отклонила доступ (401). Используйте /oura reconnect в личном чате."
    if status == 403:
        return "Oura запретила чтение данных (403). Проверьте разрешения подключения и статус подписки Oura."
    if status == 429:
        return "Oura ограничила частоту запросов (429). Попробуйте позже."
    if status is not None:
        return f"Запрос к Oura завершился ошибкой ({status}). Попробуйте позже."
    if timed_out:
        return "Oura не ответила вовремя. Попробуйте позже."
    return "Не удалось загрузить данные Oura. Попробуйте позже."


def _decode(row: OuraDailyMetric) -> dict[str, Any]:
    raw = row.raw_json or {}
    encrypted = raw.get("archive_encrypted")
    if not encrypted:
        # Existing legacy rows remain readable; raw provider data is not exposed.
        return {
            "collections": {},
            "summary": {
                "date": row.metric_date.isoformat(),
                "sleep_score": row.sleep_score,
                "readiness_score": row.readiness_score,
                "activity_score": row.activity_score,
                "steps": row.steps,
                "spo2_average_percent": row.spo2_average,
            },
            "fetched": {},
        }
    return json.loads(
        get_token_cipher().decrypt(
            encrypted,
            user_id=row.user_id,
            provider="oura",
            token_type=f"health_day:{row.metric_date.isoformat()}",
        )
    )


async def sync_and_load_health(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    timezone_name: str = "Europe/Kyiv",
    today: date | None = None,
    daily_only: bool = False,
) -> dict[str, Any]:
    actor = session.info.get("actor_context")
    if actor is not None:
        IdentityService.validate_domain_access(actor, "health")
        if actor.user_id != user_id:
            raise PermissionDeniedError("Access denied.")
    today = today or datetime.now(ZoneInfo(timezone_name)).date()
    now = datetime.now(timezone.utc)
    first = today - timedelta(days=ARCHIVE_DAYS)
    # Serialize first inserts and updates for the same user, including concurrent commands.
    user = (await session.execute(select(User).where(User.id == user_id).with_for_update())).scalar_one_or_none()
    if user is None:
        raise PermissionDeniedError("Access denied.")
    rows = list(
        (
            await session.execute(
                select(OuraDailyMetric)
                .where(
                    OuraDailyMetric.user_id == user_id,
                    OuraDailyMetric.metric_date >= first,
                    OuraDailyMetric.metric_date <= today,
                )
                .order_by(OuraDailyMetric.metric_date)
            )
        ).scalars()
    )
    snapshots = {row.metric_date: _decode(row) for row in rows}
    by_day = {row.metric_date: row for row in rows}
    connection = (
        await session.execute(
            select(HealthProviderConnection).where(
                HealthProviderConnection.user_id == user_id,
                HealthProviderConnection.provider == "oura",
            )
        )
    ).scalar_one_or_none()
    last_sync = connection.last_sync_succeeded_at if connection else None
    if last_sync is not None and last_sync.tzinfo is None:
        last_sync = last_sync.replace(tzinfo=timezone.utc)
    partial = bool(connection and connection.last_error_code)
    cached = not daily_only and last_sync is not None and now - last_sync < timedelta(minutes=5) and not partial
    failures: list[str] = []
    if not cached:
        payloads: dict[str, dict[str, Any]] = {}
        try:
            token = await HealthTools.get_valid_oura_access_token(session, user_id=user_id)
            semaphore = asyncio.Semaphore(3)

            async def fetch(name: str) -> None:
                async with semaphore:
                    try:
                        payloads[name] = await OuraClient.get_collection_range(
                            name, access_token=token, start_date=today if daily_only else first, end_date=today
                        )
                    except Exception as exc:
                        failures.append(_failure_message(exc, name))

            async with asyncio.timeout(45):
                await asyncio.gather(*(fetch(name) for name in COLLECTIONS))
            partial = len(payloads) != len(COLLECTIONS)
        except (HealthIntegrationError, TimeoutError) as exc:
            failures.append(_failure_message(exc, "sync"))
            partial = True
        if not payloads and not snapshots:
            raise HealthIntegrationError(
                "No saved Oura data is available.", user_message=" ".join(dict.fromkeys(failures)) or None
            )
        # A successful one-day fetch must not mark the entire history as freshly synced.
        if not daily_only:
            if connection is None:
                connection = HealthProviderConnection(user_id=user_id, provider="oura", status="connected")
                session.add(connection)
            connection.last_sync_started_at = now
            connection.last_error_code = "OURA_PARTIAL_OR_UNAVAILABLE" if partial else None
            connection.status = "error" if partial else "connected"
            if payloads:
                connection.last_sync_succeeded_at = now
                last_sync = now
        # Only records actually returned by Oura replace saved collections. An empty
        # or failed collection cannot erase earlier readings or fabricate a zero.
        grouped: dict[date, dict[str, list[dict[str, Any]]]] = {}
        for name, payload in payloads.items():
            for item in payload.get("data", []):
                if not isinstance(item, dict):
                    continue
                try:
                    day = date.fromisoformat(str(item.get("day", "")))
                except ValueError:
                    continue
                if first <= day <= today:
                    grouped.setdefault(day, {}).setdefault(name, []).append(item)
        for day, received in grouped.items():
            snapshot = snapshots.get(day, {"collections": {}, "fetched": {}})
            saved = snapshot["collections"]
            for name, records in received.items():
                saved[name] = {"data": records}
                snapshot["fetched"][name] = now.isoformat()
            summary = HealthTools.summarize_oura_day(saved, day)
            # Non-finite provider values are not meaningful measurements or JSON.
            summary = {
                key: None if isinstance(value, float) and not math.isfinite(value) else value
                for key, value in summary.items()
            }
            snapshot["summary"] = summary
            row = by_day.get(day)
            if row is None:
                row = OuraDailyMetric(user_id=user_id, metric_date=day)
                session.add(row)
                by_day[day] = row
            row.raw_json = {
                "archive_encrypted": get_token_cipher().encrypt(
                    json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                    user_id=user_id,
                    provider="oura",
                    token_type=f"health_day:{day.isoformat()}",
                )
            }
            # Keep the original normalized columns useful without confusing HRV
            # in milliseconds with Oura's separate HRV-balance contributor score.
            for name in ("sleep_score", "readiness_score", "activity_score"):
                value = summary.get(name)
                setattr(row, name, int(value) if isinstance(value, (int, float)) and 0 <= value <= 100 else None)
            snapshots[day] = snapshot
        await session.flush()
    summaries = []
    refreshed = []
    for day in sorted(snapshots):
        snapshot = snapshots[day]
        dates = list(snapshot.get("fetched", {}).values())
        refreshed.extend(dates)
        summaries.append({**snapshot["summary"], "archive_updated_at": max(dates) if dates else None})
    return {
        "summaries": summaries,
        "last_sync": max(refreshed) if refreshed else None,
        "partial": partial,
        "sync_error": " ".join(dict.fromkeys(failures)),
        "today": today.isoformat(),
    }


async def get_saved_daily_summary(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    timezone_name: str = "Europe/Kyiv",
) -> dict[str, Any]:
    archive = await sync_and_load_health(session, user_id=user_id, timezone_name=timezone_name, daily_only=True)
    today = archive["today"]
    summary = next((dict(row) for row in archive["summaries"] if row["date"] == today), {"date": today})
    from app.tools.health_explanation import daily_context, explain_health

    summary["sync_error"] = archive.get("sync_error") or (
        "Не удалось загрузить часть данных Oura. Попробуйте позже." if archive["partial"] else ""
    )
    summary["analysis"] = await explain_health(daily_context(summary, archive["summaries"]))
    summary["archive_status"] = (
        f"Обновление показателей дня: {summary.get('archive_updated_at') or 'нет сохранённых данных'}."
        + (" " + summary["sync_error"] if summary["sync_error"] else "")
        + " Некоторые показатели могут быть сохранены ранее."
    )
    return summary
