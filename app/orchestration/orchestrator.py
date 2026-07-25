import uuid
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestration.router import IntentRouter
from app.tools.health_tools import HealthTools
from app.tools.finance_tools import FinanceTools
from app.tools.planner_tools import PlannerTools


class MainOrchestrator:
    """Main Orchestrator Agent for Family AI Life OS."""

    @classmethod
    async def process_user_message(
        cls,
        session: AsyncSession,
        user_id: uuid.UUID,
        household_id: uuid.UUID,
        user_name: str,
        message_text: str,
        photo_bytes: Optional[bytes] = None,
        document_bytes: Optional[bytes] = None,
    ) -> str:
        """Processes incoming user input, delegates to deterministic tools, and returns response."""
        has_photo = photo_bytes is not None
        has_doc = document_bytes is not None

        routing = IntentRouter.classify_intent(message_text, has_photo=has_photo, has_document=has_doc)
        intent = routing["intent"]

        # Case 1: Food Photo Vision Analysis
        if intent == "FOOD_NUTRITION_ANALYSIS" and photo_bytes:
            res = await HealthTools.log_meal_photo(session, user_id=user_id, image_bytes=photo_bytes)
            return (
                f"🥗 **Блюдо распознано:** {res['dish_name']}\n\n"
                f"📊 **Примерная ценность:**\n"
                f"• Калории: ~{res['calories_est']} ккал\n"
                f"• Белки: {res['proteins_g']} г | Жиры: {res['fats_g']} г | Углеводы: {res['carbs_g']} г\n\n"
                f"💡 *Совет:* {res.get('coaching_tip', 'Приём пищи успешно записан!')}"
            )

        # Case 2: Financial Query or Transaction Log
        if intent == "FINANCIAL_QUERY_OR_LOG":
            # Quick check if asking for spending summary
            if "сколько" in message_text.lower() or "расходы" in message_text.lower():
                summary = await FinanceTools.get_spending_summary(session, owner_id=household_id)
                cats_formatted = "\n".join([f"• {k}: {v:.2f} UAH" for k, v in summary["categories"].items()]) or "Пока нет записанных расходов."
                return (
                    f"💳 **Общие расходы семьи в этом месяце:**\n"
                    f"Итого: **{summary['total_expense']:.2f} UAH**\n\n"
                    f"Категории:\n{cats_formatted}"
                )

        # Case 3: Shopping List & Planning
        if intent == "PLANNING_OR_REMINDER":
            if "купить" in message_text.lower() or "список" in message_text.lower():
                # Extract item name basic heuristic
                item_name = message_text.replace("Добавь", "").replace("добавь", "").replace("в список покупок", "").replace("купить", "").strip()
                if item_name:
                    res = await PlannerTools.add_shopping_item(session, household_id=household_id, added_by_id=user_id, item_name=item_name)
                    return f"🛒 Добавлено в семейный список покупок: **{res['item_name']}**"

                items = await PlannerTools.get_active_shopping_list(session, household_id=household_id)
                if not items:
                    return "🛒 Ваш семейный список покупок пока пуст!"
                formatted = "\n".join([f"• {i['item_name']}" for i in items])
                return f"🛒 **Текущий список покупок:**\n{formatted}"

        # Default fallback response
        return (
            f"Привет, {user_name}! Я ваш семейный ассистент Family AI Life OS.\n"
            f"Я принял ваш запрос. Какую задачу мы выполняем сегодня?"
        )
