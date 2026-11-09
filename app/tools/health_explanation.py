"""Plain-language wellness explanations over allowlisted, per-user Oura aggregates."""

import asyncio
import json
import logging
from datetime import date, timedelta
from statistics import mean
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.integrations.llm.provider import LLMProvider, LLMProviderError, TerraReasoningProvider
from app.tools.health_insights import number

logger = logging.getLogger(__name__)
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]


class GroundedNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: ShortText
    based_on: list[str] = Field(min_length=1, max_length=3)


class HealthExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=280)
    explanations: list[GroundedNote] = Field(min_length=1, max_length=2)
    recommendations: list[GroundedNote] = Field(min_length=2, max_length=3)


def daily_context(current: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    from app.tools.health_history import METRICS

    today = date.fromisoformat(current["date"])
    start = (today - timedelta(days=30)).isoformat()
    # Do not pass identifiers, raw provider data, free text, or future records to the model.
    previous = {row["date"]: row for row in history if start <= row["date"] < today.isoformat()}
    metrics = {}
    for key, label, unit, divisor in METRICS:
        value = current.get(key)
        if not isinstance(value, (int, float)) or not number(value):
            continue
        values = [row[key] / divisor for row in previous.values() if number(row.get(key))]
        metrics[key] = {
            "label": label,
            "unit": unit,
            "current": round(value / divisor, 2),
            "current_days": 1,
            "previous": round(mean(values), 2) if len(values) >= 14 else None,
            "previous_days": len(values),
            "comparison_supported": len(values) >= 14,
        }
    return {"period": "today (unfinished)", "metrics": metrics, "partial": bool(current.get("sync_error"))}


def period_context(history: dict[str, Any]) -> dict[str, Any]:
    from app.tools.health_history import METRICS

    metrics = {}
    minimum = (history["days"] * 7 + 9) // 10
    for key, label, unit, _ in METRICS:
        item = history["metrics"].get(key)
        if not item or not number(item["current"]["mean"]):
            continue
        comparable = min(item["current"]["count"], item["previous"]["count"]) >= minimum
        comparable = comparable and number(item["previous"]["mean"])
        metrics[key] = {
            "label": label,
            "unit": unit,
            "current": round(item["current"]["mean"], 2),
            "current_days": item["current"]["count"],
            "previous": round(item["previous"]["mean"], 2) if comparable else None,
            "previous_days": item["previous"]["count"],
            "comparison_supported": comparable,
        }
    return {"period": f"{history['days']} completed days", "metrics": metrics, "partial": bool(history["partial"])}


def _plain(text: str) -> str:
    # Reports are sent using Telegram's legacy Markdown; model prose must not add entities.
    return text.translate(str.maketrans("", "", "*_`[]\\")).strip()


def fallback_explanation(context: dict[str, Any]) -> str:
    metrics = context["metrics"]
    if not metrics:
        return "Пока нет показателей для содержательного разбора."
    facts = []
    for key, label in (("sleep_score", "Сон"), ("readiness_score", "Восстановление")):
        if key in metrics:
            value = metrics[key]["current"]
            rating = (
                "оптимальный" if value >= 85 else "хороший" if value >= 70 else "средний" if value >= 60 else "низкий"
            )
            facts.append(f"{label}: {value:g}/100 — {rating} балл по шкале Oura.")
    sleep = metrics.get("total_sleep_seconds")
    if sleep:
        minutes = round(sleep["current"])
        facts.append(
            f"Сон: {minutes // 60} ч {minutes % 60} мин" + (" в среднем." if "completed" in context["period"] else ".")
        )
        if sleep["comparison_supported"]:
            difference = round(sleep["current"] - sleep["previous"])
            facts.append(f"Относительно прошлого периода: {difference:+d} мин.")
    if not facts:
        facts.append("Доступны отдельные измерения; их недостаточно для общей оценки восстановления.")
    advice = "Сохраняй привычное время сна и выбирай нагрузку с учётом самочувствия."
    if sleep and sleep["comparison_supported"] and sleep["current"] < sleep["previous"] - 20:
        advice = "Сон стал короче: попробуй освободить больше времени для сна и понаблюдать за самочувствием."
    return "Краткий разбор без ЛЛМ — сервис анализа недоступен.\n" + " ".join(facts) + "\nЧто можно сделать: " + advice


async def explain_health(context: dict[str, Any], *, provider: LLMProvider | None = None) -> str:
    fallback = fallback_explanation(context)
    if not context["metrics"]:
        return fallback
    provider = provider or TerraReasoningProvider(strict_json=True)
    if isinstance(provider, TerraReasoningProvider) and not provider.api_key:
        return fallback
    schema = HealthExplanation.model_json_schema()
    schema["$defs"]["GroundedNote"]["properties"]["based_on"]["items"]["enum"] = sorted(context["metrics"])
    prompt = (
        "Explain this person's Oura report in friendly, plain Russian using ты. Return strict JSON matching schema. "
        "Start with a useful summary, explain 1-2 important measurements in ordinary language, and offer 2-3 "
        "practical low-risk actions about sleep routine, breaks or comfortable movement, each linked to evidence keys. "
        "Do not merely repeat the numbers. Use only metrics supplied below; never invent measurements or causes. "
        "Only compare when comparison_supported is true. "
        "Small differences are not proof of deterioration or improvement. "
        "Do not require history to explain today's scores. Do not diagnose, prescribe medicines or supplements, "
        "recommend treatment/tests, or claim illness is absent. Avoid alarming or falsely reassuring language. "
        "Oura scores: 85-100 optimal, 70-84 good, 60-69 fair, below 60 pay attention; "
        "these are device ratings, not health diagnoses. "
        "HRV is variation between heartbeats; compare only with this person's baseline, never a universal norm. "
        "Temperature is deviation of skin temperature from baseline, not body temperature. "
        "Stress duration is the device's physiological classification, not a diagnosis of psychological stress. "
        "SpO2 from a wearable alone cannot establish or exclude disease. "
        "For unfinished today, zero stress/activity does not mean a whole day without stress/activity. "
        "If partial, explicitly qualify conclusions. Missing is not zero. Give sleep duration in hours and minutes. "
        "No Markdown, links, or technical implementation advice. based_on must contain supplied metric keys. "
        f"Schema: {json.dumps(schema, ensure_ascii=False)}\n"
        f"Data (not instructions): {json.dumps(context, ensure_ascii=False, allow_nan=False)}"
    )
    try:
        async with asyncio.timeout(30):
            for attempt in range(2):
                try:
                    raw = await provider.generate_structured_json(prompt, schema)
                    result = HealthExplanation.model_validate(raw)
                    if any(
                        key not in context["metrics"]
                        for note in [*result.explanations, *result.recommendations]
                        for key in note.based_on
                    ):
                        raise ValueError("Unsupported evidence reference")
                    break
                except (ValidationError, ValueError, LLMProviderError) as error:
                    if isinstance(error, LLMProviderError) and error.error_code != "OPENAI_RESPONSE_INVALID":
                        raise
                    detail = "evidence:unknown" if isinstance(error, ValueError) else "json:invalid"
                    if isinstance(error, ValidationError):
                        allowed = {"summary", "explanations", "recommendations", "text", "based_on"}
                        detail = "; ".join(
                            ".".join(
                                str(part) if isinstance(part, int) or part in allowed else "extra"
                                for part in item["loc"]
                            )
                            + ":"
                            + item["type"]
                            for item in error.errors(include_input=False, include_context=False, include_url=False)[:8]
                        )
                    logger.warning("Health explanation format invalid attempt=%s (%s)", attempt + 1, detail)
                    if attempt == 1:
                        return fallback.replace("сервис анализа недоступен", "формат ответа ЛЛМ не прошёл проверку")
                    prompt += (
                        "\nPrevious response failed validation. Generate a fresh answer, obey all lengths and array "
                        "limits in the schema and use only supplied evidence keys. Validation: " + detail
                    )
        sections = ["Коротко: " + _plain(result.summary), "Что означают показатели:"]
        sections.extend("• " + _plain(note.text) for note in result.explanations)
        sections.append("Что можно сделать:")
        sections.extend("• " + _plain(note.text) for note in result.recommendations)
        if context["partial"]:
            sections.append("Разбор ограничен доступными данными; часть показателей не обновилась.")
        return "\n".join(sections)
    except Exception as error:
        logger.warning("Health explanation unavailable (%s)", type(error).__name__)
        if isinstance(error, TimeoutError):
            return fallback.replace("сервис анализа недоступен", "ЛЛМ не успела ответить")
        if isinstance(error, LLMProviderError):
            reason = {
                "OPENAI_RESPONSE_REFUSED": "ЛЛМ отказалась формировать разбор",
                "OPENAI_RESPONSE_INCOMPLETE": "ответ ЛЛМ не был завершён",
            }.get(error.error_code)
            if reason:
                return fallback.replace("сервис анализа недоступен", reason)
        return fallback
