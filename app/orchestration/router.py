from typing import Any


class IntentRouter:
    """Classifies user messages and selects destination agents."""

    @staticmethod
    def classify_intent(message_text: str, has_photo: bool = False, has_document: bool = False) -> dict[str, Any]:
        text = message_text.lower() if message_text else ""

        if has_photo:
            return {
                "intent": "FOOD_NUTRITION_ANALYSIS"
                if any(w in text for w in ["еда", "обед", "ужин", "завтрак", "калории", "кушать"]) or not text
                else "RECEIPT_OCR",
                "primary_agent": "health"
                if any(w in text for w in ["еда", "обед", "ужин", "завтрак", "калории"]) or not text
                else "documents",
                "secondary_agents": ["finance"]
                if not any(w in text for w in ["еда", "обед", "ужин", "завтрак"])
                else [],
            }

        if has_document:
            return {
                "intent": "DOCUMENT_PROCESSING",
                "primary_agent": "documents",
                "secondary_agents": ["health", "finance"],
            }

        # Health Domain Patterns
        if any(
            w in text
            for w in ["сон", "оура", "oura", "пульс", "готовность", "readiness", "здоровье", "самочувствие", "анализ"]
        ):
            return {
                "intent": "HEALTH_BIOMETRICS_QUERY",
                "primary_agent": "health",
                "secondary_agents": [],
            }

        # Finance Domain Patterns
        relative_spending_question = "сколько" in text and any(
            day in text for day in ("сегодня", "вчера", "позавчера", "недел")
        )
        named_expense_question = any(
            marker in text
            for marker in (
                "сегодняшн",
                "вчерашн",
                "записан",
                "записал",
                "учтен",
                "учтён",
                "учете",
                "учёте",
            )
        ) and any(
            subject in text
            for subject in (
                "кофе",
                "корм",
                "отдых",
                "покупк",
                "продукт",
                "такси",
                "бензин",
                "кафе",
                "ресторан",
            )
        )
        if (
            relative_spending_question
            or named_expense_question
            or any(
                w in text
                for w in [
                    "потратили",
                    "трат",
                    "расходы",
                    "бюджет",
                    "купили",
                    "чек",
                    "цена",
                    "стоит",
                    "деньги",
                    "грн",
                    "долларов",
                    "таблиц",
                    "google sheet",
                ]
            )
        ):
            return {
                "intent": "FINANCIAL_QUERY_OR_LOG",
                "primary_agent": "finance",
                "secondary_agents": ["documents"],
            }

        # Planning Domain Patterns
        if (
            any(
                w in text
                for w in [
                    "напомн",
                    "напомин",
                    "напомни",
                    "не забыть",
                    "нужно завтра",
                    "надо завтра",
                    "задач",
                    "список покупок",
                    "купить",
                    "план",
                    "встреча",
                    "стоматолог",
                    "календарь",
                ]
            )
            or (
                any(marker in text for marker in ("каждый день", "ежедневно", "ежедневный"))
                and any(marker in text for marker in ("добав", "созда", "постав", "запиш", "не забыть"))
            )
            or (
                any(marker in text for marker in ("запиш", "постав", "добав", "созда"))
                and any(
                    marker in text
                    for marker in ("сегодня", "завтра", "послезавтра", "в понедельник", "вторник", "стрижк")
                )
            )
        ):
            return {
                "intent": "PLANNING_OR_REMINDER",
                "primary_agent": "planner",
                "secondary_agents": ["notifications"],
            }

        # Memory / General Query Pattern
        return {
            "intent": "GENERAL_FAMILY_ASSISTANT",
            "primary_agent": "orchestrator",
            "secondary_agents": ["memory"],
        }
