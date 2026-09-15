"""Descriptive Oura period statistics; missing readings are never treated as zero."""

import math
import uuid
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.health_archive import sync_and_load_health
from app.tools.health_insights import period_insight

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
    ("deep_sleep_seconds", "Глубокий сон", "мин", 60),
    ("rem_sleep_seconds", "REM-сон", "мин", 60),
    ("sleep_efficiency", "Эффективность сна", "%", 1),
    ("sleep_latency_seconds", "Засыпание", "мин", 60),
    ("average_breath_per_minute", "Дыхание во сне", "вдох/мин", 1),
    ("temperature_deviation_c", "Отклонение температуры кожи", "°C", 1),
    ("recovery_high", "Восстановление за день", "мин", 60),
    ("active_calories", "Активные калории", "ккал", 1),
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
    archive = await sync_and_load_health(session, user_id=user_id, timezone_name=timezone_name, today=today)
    return summarize_history(archive, days=days, today=today)


def summarize_history(archive: dict[str, Any], *, days: int, today: date) -> dict[str, Any]:
    first = today - timedelta(days=2 * days)
    by_date = {row["date"]: row for row in archive["summaries"]}
    summaries = [by_date.get((first + timedelta(days=i)).isoformat(), {}) for i in range(2 * days)]
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
        "partial": archive["partial"],
        "last_sync": archive.get("last_sync"),
    }


def format_health_history(history: dict[str, Any]) -> str:
    days = history["days"]

    def display_date(value: str) -> str:
        return date.fromisoformat(value).strftime("%d.%m.%Y")

    lines = [
        f"📊 Здоровье за {days} дней: {display_date(history['start'])}–{display_date(history['end'])}",
        f"Сравнение: {display_date(history['previous_start'])}–{display_date(history['previous_end'])}.",
        "Средние значения; в скобках — число дней с данными.",
        "\n🤖 " + period_insight(history),
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
    if history.get("last_sync"):
        lines.append(f"Обновление архива: {history['last_sync']}.")
    return "\n".join(lines)
