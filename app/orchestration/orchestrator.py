import uuid
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestration.router import IntentRouter
from app.tools.finance_tools import FinanceTools
from app.tools.health_tools import HealthTools
from app.tools.planner_tools import PlannerTools


class MainOrchestrator:
    """Main Orchestrator Agent for Family AI Life OS."""

    @staticmethod
    def _routing_text(message_text: str) -> str:
        text = (message_text or "").strip()
        command = text.split(maxsplit=1)[0].lower() if text else ""
        command_route = {
            "/budget": "расходы",
            "/shopping": "список покупок",
            "/tasks": "задача",
            "/health": "здоровье",
        }.get(command)
        if command_route:
            return command_route
        if "расход" in text.lower():
            return f"расходы {text}"
        return text

    @staticmethod
    def domain_for_message(
        message_text: str,
        *,
        has_photo: bool = False,
        has_document: bool = False,
    ) -> str:
        """Return the authorization domain before any tool is executed."""
        routing = IntentRouter.classify_intent(
            MainOrchestrator._routing_text(message_text),
            has_photo=has_photo,
            has_document=has_document,
        )
        return {
            "health": "health",
            "finance": "finance",
            "planner": "planner",
            "documents": "medical_docs",
            "memory": "personal_memory",
            "orchestrator": "general",
        }.get(routing["primary_agent"], "general")

    @staticmethod
    def current_month_bounds(
        *,
        now: datetime | None = None,
        timezone_name: str = "Europe/Kyiv",
    ) -> tuple[datetime, datetime]:
        """Return UTC bounds for the current local calendar month."""
        zone = ZoneInfo(timezone_name)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        local_now = current.astimezone(zone)
        month_start = local_now.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        if month_start.month == 12:
            next_month = month_start.replace(
                year=month_start.year + 1,
                month=1,
            )
        else:
            next_month = month_start.replace(month=month_start.month + 1)
        return (
            month_start.astimezone(timezone.utc),
            next_month.astimezone(timezone.utc),
        )

    @staticmethod
    def _format_spending_summary(summary: dict) -> str:
        currencies = summary.get("currencies", {})
        if not currencies:
            return "💳 **Общие расходы семьи в этом месяце:**\nПока нет записанных расходов."

        sections: list[str] = []
        for currency, currency_summary in sorted(currencies.items()):
            total = Decimal(str(currency_summary.get("total_expense", "0")))
            categories = currency_summary.get("categories", {})
            category_lines = "\n".join(
                f"• {category}: {Decimal(str(amount)):.2f} {currency}"
                for category, amount in sorted(categories.items())
            )
            if not category_lines:
                category_lines = "• Без категорий"
            sections.append(f"**{total:.2f} {currency}**\n{category_lines}")

        return "💳 **Общие расходы семьи в этом месяце:**\n\n" + "\n\n".join(sections)

    @classmethod
    async def process_user_message(
        cls,
        session: AsyncSession,
        user_id: uuid.UUID,
        household_id: uuid.UUID,
        user_name: str,
        message_text: str,
        photo_bytes: bytes | None = None,
        document_bytes: bytes | None = None,
        timezone_name: str = "Europe/Kyiv",
    ) -> str:
        """Processes incoming user input, delegates to deterministic tools, and returns response."""
        has_photo = photo_bytes is not None
        has_doc = document_bytes is not None

        routing = IntentRouter.classify_intent(
            cls._routing_text(message_text),
            has_photo=has_photo,
            has_document=has_doc,
        )
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
            if (
                "сколько" in message_text.lower()
                or "расходы" in message_text.lower()
                or message_text.lower().startswith("/budget")
            ):
                date_from, date_to = cls.current_month_bounds(timezone_name=timezone_name)
                summary = await FinanceTools.get_spending_summary(
                    session,
                    owner_id=household_id,
                    date_from=date_from,
                    date_to=date_to,
                )
                return cls._format_spending_summary(summary)

        # Case 3: Shopping List & Planning
        if intent == "PLANNING_OR_REMINDER":
            if (
                "купить" in message_text.lower()
                or "список" in message_text.lower()
                or message_text.lower().startswith("/shopping")
            ):
                # Extract item name basic heuristic
                item_name = (
                    ""
                    if message_text.lower().startswith("/shopping")
                    else message_text.replace("Добавь", "")
                    .replace("добавь", "")
                    .replace("в список покупок", "")
                    .replace("купить", "")
                    .strip()
                )
                if item_name:
                    res = await PlannerTools.add_shopping_item(
                        session,
                        household_id=household_id,
                        added_by_id=user_id,
                        item_name=item_name,
                    )
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
