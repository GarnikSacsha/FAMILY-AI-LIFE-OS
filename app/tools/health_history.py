"""Descriptive Oura period statistics; missing readings are never treated as zero."""

import asyncio
import math
import uuid
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.oura.client import OuraClient, OuraOAuthError
from app.tools.health_tools import HealthIntegrationError, HealthTools

METRICS = (
    ("sleep_score", "😴 Оценка сна", "балл", 1),
    ("readiness_score", "❤️ Восстановление", "балл", 1),
    ("activity_score", "🏃 Оценка активности", "балл", 1),
    ("total_sleep_seconds", "Сон за ночь", "мин", 60),
    ("average_hrv_ms", "HRV", "мс", 1),
    ("lowest_heart_rate_bpm", "Минимальный пульс во сне", "уд/мин", 1),
    ("steps", "Шаги за день", "шаг", 1),
    ("spo2_average_percent", "SpO₂", "%", 1),
    ("stress_high", "Высокий стресс за день", "мин", 60),
)


async def get_health_history(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    days: int,
    timezone_name: str = "Europe/Kyiv",
    today: date | None = None,
) -> dict[str, Any]:
    if days not in {7, 30}:
        raise ValueError("Health history supports 7 or 30 days.")
    today = today or datetime.now(ZoneInfo(timezone_name)).date()
    first = today - timedelta(days=2 * days)
    token = await HealthTools.get_valid_oura_access_token(session, user_id=user_id)
    collections = ("daily_sleep", "daily_readiness", "daily_activity", "sleep", "daily_spo2", "daily_stress")
    results = await asyncio.gather(
        *(
            OuraClient.get_collection_range(name, access_token=token, start_date=first, end_date=today)
            for name in collections
        ),
        return_exceptions=True,
    )
    if any(isinstance(result, OuraOAuthError) and result.status_code == 401 for result in results):
        raise HealthIntegrationError("Oura connection must be renewed.")
    payloads = {name: result for name, result in zip(collections, results, strict=True) if isinstance(result, dict)}
    if not any(name in payloads for name in collections[:3]):
        raise HealthIntegrationError("Oura history is temporarily unavailable.")
    # Explicit local date bounds exclude the incomplete current day and deduplicate daily records.
    summaries = [HealthTools.summarize_oura_day(payloads, first + timedelta(days=i)) for i in range(2 * days)]
    metrics = {}
    for key, _, _, divisor in METRICS:
        periods = []
        for records in (summaries[:days], summaries[days:]):
            values = [
                float(row[key]) / divisor
                for row in records
                if isinstance(row.get(key), (int, float)) and not isinstance(row[key], bool) and math.isfinite(row[key])
            ]
            periods.append({"mean": sum(values) / len(values) if values else None, "count": len(values)})
        metrics[key] = {"previous": periods[0], "current": periods[1]}
    return {
        "days": days,
        "start": (today - timedelta(days=days)).isoformat(),
        "end": (today - timedelta(days=1)).isoformat(),
        "previous_start": first.isoformat(),
        "previous_end": (today - timedelta(days=days + 1)).isoformat(),
        "metrics": metrics,
        "partial": len(payloads) < len(collections),
    }


def format_health_history(history: dict[str, Any]) -> str:
    days = history["days"]

    def display_date(value: str) -> str:
        return date.fromisoformat(value).strftime("%d.%m.%Y")

    lines = [
        f"📊 Здоровье за {days} дней: {display_date(history['start'])}–{display_date(history['end'])}",
        f"Сравнение: {display_date(history['previous_start'])}–{display_date(history['previous_end'])}.",
        "Средние значения; в скобках — число дней с данными.",
    ]
    any_data = False
    for key, label, unit, _ in METRICS:
        current = history["metrics"][key]["current"]
        previous = history["metrics"][key]["previous"]
        if current["mean"] is None:
            lines.append(f"\n{label}: нет данных")
            continue
        any_data = True
        line = f"\n{label}: {current['mean']:.1f} {unit} ({current['count']}/{days})"
        if previous["mean"] is not None:
            delta = current["mean"] - previous["mean"]
            line += f"\nРанее: {previous['mean']:.1f} ({previous['count']}/{days}); разница {delta:+.1f} {unit}."
        else:
            line += "\nДля предыдущего периода данных нет."
        lines.append(line)
    if not any_data:
        lines.append("\nЗа этот период Oura не вернула показатели. Проверьте синхронизацию кольца.")
    if history["partial"]:
        lines.append("\nЧасть данных Oura сейчас недоступна. Сводка неполная; попробуйте позже.")
    lines.append("\nСегодняшний незавершённый день не включён. При пропусках сравнение может быть неточным.")
    return "\n".join(lines)
