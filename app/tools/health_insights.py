"""Short, evidence-based descriptions of personal trends, not clinical thresholds."""

import math
from datetime import date, timedelta
from statistics import mean
from typing import Any


def number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def daily_insight(current: dict[str, Any], history: list[dict[str, Any]]) -> str:
    day = date.fromisoformat(current["date"])
    # Compare three consecutive days including today to the preceding 30 days.
    # Missing days break the sequence; they never become evidence of persistence.
    by_day = {row["date"]: row for row in history}
    recent = [by_day.get((day - timedelta(days=i)).isoformat(), {}) for i in range(3)]
    baseline = [
        row
        for key, row in by_day.items()
        if (day - timedelta(days=32)).isoformat() <= key < (day - timedelta(days=2)).isoformat()
    ]
    notes = []
    comparisons = 0
    for key, label, direction, threshold, unit, divisor in (
        ("total_sleep_seconds", "Сон", -1, 45 * 60, "мин", 60),
        ("lowest_heart_rate_bpm", "Ночной минимальный пульс", 1, 5, "уд/мин", 1),
        ("average_hrv_ms", "HRV", -1, None, "мс", 1),
    ):
        values = [row[key] for row in baseline if number(row.get(key))]
        if len(values) < 14 or not number(current.get(key)):
            continue
        comparisons += 1
        average = mean(values)
        tolerance = threshold if threshold is not None else abs(average) * 0.2
        if tolerance <= 0:
            continue
        if all(number(row.get(key)) and direction * (row[key] - average) >= tolerance for row in recent):
            word = "выше" if direction > 0 else "ниже"
            notes.append(
                f"{label} {word} вашего среднего 3 дня подряд "
                f"(сегодня {current[key] / divisor:.0f}, обычно {average / divisor:.0f} {unit}; {len(values)} дней)."
            )
    if notes:
        return (
            " ".join(notes[:2])
            + " Понаблюдайте за сном, нагрузкой и самочувствием; по кольцу причину определить нельзя."
        )
    if not any(
        number(current.get(key)) for key in ("sleep_score", "readiness_score", "total_sleep_seconds", "average_hrv_ms")
    ):
        return "Сегодня пока недостаточно показателей для разбора. Синхронизируйте Oura позже."
    if comparisons == 0:
        return "Личной истории пока мало: для сравнения нужно хотя бы 14 дней с соответствующими показателями."
    return (
        "По доступным сну, HRV и пульсу выбранные устойчивые отклонения за три дня не выявлены. "
        "Это не оценка здоровья в целом."
    )


def period_insight(history: dict[str, Any]) -> str:
    minimum = math.ceil(history["days"] * 0.7)
    candidates = []
    labels = {
        "total_sleep_seconds": ("Сон", "мин"),
        "average_hrv_ms": ("HRV", "мс"),
        "lowest_heart_rate_bpm": ("Ночной пульс", "уд/мин"),
        "readiness_score": ("Восстановление", "балла"),
    }
    for key, (label, unit) in labels.items():
        metric = history["metrics"].get(key)
        if metric is None:
            continue
        current, previous = metric["current"], metric["previous"]
        if min(current["count"], previous["count"]) < minimum:
            continue
        if not number(current["mean"]) or not number(previous["mean"]):
            continue
        delta = current["mean"] - previous["mean"]
        candidates.append(
            (abs(delta) / max(abs(previous["mean"]), 1), f"{label}: {delta:+.1f} {unit} к предыдущему периоду.")
        )
    if not candidates:
        return "Для надёжного сравнения пока мало данных в одном из периодов. Ниже — только доступные измерения."
    candidates.sort(reverse=True)
    return (
        " ".join(text for _, text in candidates[:2])
        + " Следите, сохраняются ли изменения; причина по этим данным не определяется."
    )
