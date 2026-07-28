import json
from typing import Any

from app.integrations.llm.provider import TerraReasoningProvider

SUMMARY_KEYS = (
    "decisions",
    "actions",
    "money",
    "open_questions",
    "facts",
    "suggestions",
)


def _clean_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = " ".join(item.strip().split())
        if normalized and normalized not in result:
            result.append(normalized[:500])
    return result[:12]


def normalize_summary_data(value: dict[str, Any]) -> dict[str, list[str]]:
    return {key: _clean_items(value.get(key)) for key in SUMMARY_KEYS}


def format_summary(data: dict[str, list[str]], *, daily: bool = False) -> str:
    labels = {
        "decisions": "✅ Решили",
        "actions": "📌 Нужно сделать",
        "money": "💳 Деньги",
        "open_questions": "❓ Осталось открытым",
        "facts": "🧠 Запомнил",
        "suggestions": "💡 Можно сделать",
    }
    blocks = ["📋 Семейная сводка за день" if daily else "📋 Выжимка разговора"]
    for key in SUMMARY_KEYS:
        items = data.get(key, [])
        if not items:
            continue
        blocks.append(labels[key] + "\n" + "\n".join(f"• {item}" for item in items))
    if len(blocks) == 1:
        blocks.append("В этом фрагменте не было решений, задач или открытых вопросов.")
    return "\n\n".join(blocks)


class SharedMemoryAgent:
    """Extract evidence-backed shared-chat highlights without executing suggestions."""

    def __init__(self, provider: TerraReasoningProvider | None = None):
        self.provider = provider or TerraReasoningProvider()

    async def summarize_messages(
        self,
        messages: list[dict[str, str]],
    ) -> dict[str, list[str]]:
        transcript = "\n".join(
            f"{index}. [{item['author']}] {item['content']}"
            for index, item in enumerate(messages, start=1)
        )
        result = await self.provider.generate_structured_json(
            prompt=(
                "Extract a factual recap from this authorized shared family-chat transcript.\n"
                "Return one strict JSON object with exactly these array-of-string keys: "
                "decisions, actions, money, open_questions, facts, suggestions.\n"
                "Use only information explicitly supported by the transcript. Respect negation and completed actions. "
                "Do not turn jokes, hypotheticals, or vague wishes into facts. "
                "Explicit reminders belong in actions; unresolved plans belong in open_questions. "
                "Suggestions must be optional and must never claim an action was executed. "
                "Keep each item concise, preserve names, amounts, dates, and times.\n\n"
                f"<shared_chat>\n{transcript}\n</shared_chat>"
            ),
            schema={
                "type": "object",
                "required": list(SUMMARY_KEYS),
                "properties": {
                    key: {"type": "array", "items": {"type": "string"}}
                    for key in SUMMARY_KEYS
                },
            },
        )
        return normalize_summary_data(result)

    async def summarize_existing_summaries(
        self,
        summaries: list[str],
    ) -> dict[str, list[str]]:
        return await self.summarize_messages(
            [
                {
                    "author": "Сохранённая выжимка",
                    "content": summary,
                }
                for summary in summaries
            ]
        )

    @staticmethod
    def as_json(data: dict[str, list[str]]) -> str:
        return json.dumps(data, ensure_ascii=False, sort_keys=True)
