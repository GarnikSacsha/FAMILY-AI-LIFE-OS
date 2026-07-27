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
        if any(
            w in text
            for w in [
                "потратили",
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
        ):
            return {
                "intent": "FINANCIAL_QUERY_OR_LOG",
                "primary_agent": "finance",
                "secondary_agents": ["documents"],
            }

        # Planning Domain Patterns
        if any(
            w in text
            for w in [
                "напомн",
                "напомин",
                "задача",
                "список покупок",
                "купить",
                "план",
                "встреча",
                "стоматолог",
                "календарь",
            ]
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
