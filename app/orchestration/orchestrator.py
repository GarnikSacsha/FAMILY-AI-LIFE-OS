import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestration.router import IntentRouter
from app.tools.finance_tools import FinanceTools
from app.tools.health_tools import HealthIntegrationError, HealthTools
from app.tools.planner_tools import PlannerTools


class MainOrchestrator:
    """Main Orchestrator Agent for Family AI Life OS."""

    _EXPENSE_AMOUNT = re.compile(
        r"(?P<amount>\d+(?:[.,]\d{1,2})?)\s*(?:грн(?:\.|ивен|ивні)?|uah|₴)\b",
        re.IGNORECASE,
    )
    _EXPENSE_PREFIX = re.compile(
        r"\b(?:запиши|записать|добавь|добавить|пожалуйста|"
        r"мои|наши|траты|расходы|сегодняшние|сегодня|за)\b",
        re.IGNORECASE,
    )

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
    def _extract_expense(cls, message_text: str) -> tuple[float, str] | None:
        amount_match = cls._EXPENSE_AMOUNT.search(message_text)
        if amount_match is None:
            return None

        amount = float(amount_match.group("amount").replace(",", "."))
        merchant_source = message_text[: amount_match.start()].strip(" \t\n,;:—-")
        merchant_parts = [part.strip() for part in re.split(r"[,;:\n]", merchant_source) if part.strip()]
        merchant = merchant_parts[-1] if merchant_parts else merchant_source
        merchant = cls._EXPENSE_PREFIX.sub(" ", merchant)
        merchant = " ".join(merchant.split()).strip(" \t\n,;:—-")
        return amount, merchant or "Расход"

    @staticmethod
    async def _generate_general_response(message_text: str, user_name: str) -> str:
        from app.integrations.llm.provider import TerraReasoningProvider

        provider = TerraReasoningProvider()
        return await provider.generate_text(
            prompt=message_text,
            system_instruction=(
                "Role: You are Family AI Life OS, the private family assistant for Denys and Oleksandra. "
                f"You are speaking with {user_name}. "
                "Personality: warm, natural, attentive, practical, and lightly humorous when appropriate. "
                "Goal: respond helpfully to the user's current message and maintain a genuine conversation. "
                "Constraints: never claim that a payment, database change, task, reminder, health action, "
                "or external operation was completed unless a deterministic application tool confirmed it. "
                "Do not diagnose medical conditions. Protect private family information. "
                "Output: answer in the language used by the user, as concise plain text without Markdown."
            ),
        )

    @staticmethod
    def _format_duration(seconds: object) -> str | None:
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
            return None
        total_minutes = max(0, round(seconds / 60))
        hours, minutes = divmod(total_minutes, 60)
        if hours and minutes:
            return f"{hours}ч {minutes}м"
        if hours:
            return f"{hours}ч"
        return f"{minutes}м"

    @classmethod
    def _format_oura_summary(cls, summary: dict[str, object]) -> str:
        month_names = (
            "",
            "января",
            "февраля",
            "марта",
            "апреля",
            "мая",
            "июня",
            "июля",
            "августа",
            "сентября",
            "октября",
            "ноября",
            "декабря",
        )
        try:
            summary_day = datetime.strptime(str(summary["date"]), "%Y-%m-%d").date()
            heading_date = f"{summary_day.day} {month_names[summary_day.month]} {summary_day.year}"
        except (KeyError, ValueError):
            heading_date = str(summary.get("date", "сегодня"))

        def value_text(value: object, suffix: str = "") -> str:
            if value is None:
                return "нет данных"
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            return f"{value}{suffix}"

        sleep_lines = [f"• Score: {value_text(summary.get('sleep_score'))}"]
        sleep_fields = (
            ("Спал", "total_sleep_seconds"),
            ("Deep", "deep_sleep_seconds"),
            ("REM", "rem_sleep_seconds"),
            ("Awake", "awake_seconds"),
        )
        for label, key in sleep_fields:
            duration = cls._format_duration(summary.get(key))
            if duration is not None:
                sleep_lines.append(f"• {label}: {duration}")
        if summary.get("sleep_efficiency") is not None:
            sleep_lines.append(f"• Эффективность: {value_text(summary['sleep_efficiency'], '%')}")

        readiness_lines = [f"• Readiness: {value_text(summary.get('readiness_score'))}"]
        if summary.get("average_hrv_ms") is not None:
            readiness_lines.append(f"• HRV: {value_text(summary['average_hrv_ms'], ' ms')}")
        if summary.get("lowest_heart_rate_bpm") is not None:
            readiness_lines.append(f"• Min HR: {value_text(summary['lowest_heart_rate_bpm'], ' bpm')}")
        temperature = summary.get("temperature_deviation_c")
        if isinstance(temperature, (int, float)) and not isinstance(temperature, bool):
            readiness_lines.append(f"• Температура: {temperature:+g}°C")
        if summary.get("spo2_average_percent") is not None:
            readiness_lines.append(f"• SpO₂: {value_text(summary['spo2_average_percent'], '%')}")

        activity_values = (
            summary.get("activity_score"),
            summary.get("steps"),
            summary.get("active_calories"),
            summary.get("total_calories"),
            summary.get("high_activity_seconds"),
            summary.get("medium_activity_seconds"),
        )
        activity_lines: list[str] = []
        if any(value is not None for value in activity_values):
            activity_lines.append(f"• Score: {value_text(summary.get('activity_score'))}")
            if summary.get("steps") is not None:
                activity_lines.append(f"• Шаги: {value_text(summary['steps'])}")
            if summary.get("total_calories") is not None:
                activity_lines.append(f"• Калории: {value_text(summary['total_calories'], ' ккал')}")
            if summary.get("active_calories") is not None:
                activity_lines.append(f"• Активные калории: {value_text(summary['active_calories'], ' ккал')}")
            high_seconds = summary.get("high_activity_seconds") or 0
            medium_seconds = summary.get("medium_activity_seconds") or 0
            if isinstance(high_seconds, (int, float)) and isinstance(medium_seconds, (int, float)):
                active_duration = cls._format_duration(high_seconds + medium_seconds)
                if active_duration is not None:
                    activity_lines.append(f"• Средняя/высокая активность: {active_duration}")
        else:
            activity_lines.append("• Данные за этот день ещё не сформированы Oura")

        blocks = [
            f"📅 {heading_date}",
            "😴 Сон\n" + "\n".join(sleep_lines),
            "❤️ Восстановление\n" + "\n".join(readiness_lines),
            "🏃 Активность\n" + "\n".join(activity_lines),
        ]

        stress_summary = summary.get("stress_summary")
        stress_high = summary.get("stress_high")
        recovery_high = summary.get("recovery_high")
        if any(value is not None for value in (stress_summary, stress_high, recovery_high)):
            stress_names = {
                "restored": "Восстановительный день",
                "normal": "Обычный уровень",
                "stressful": "Повышенная нагрузка",
            }
            stress_lines = []
            if stress_summary is not None:
                stress_lines.append(f"• Сводка: {stress_names.get(str(stress_summary), stress_summary)}")
            if stress_high is not None:
                stress_duration = cls._format_duration(stress_high)
                if stress_duration is not None:
                    stress_lines.append(f"• Высокий стресс: {stress_duration}")
            if recovery_high is not None:
                recovery_duration = cls._format_duration(recovery_high)
                if recovery_duration is not None:
                    stress_lines.append(f"• Восстановление: {recovery_duration}")
            blocks.append("🧠 Стресс\n" + "\n".join(stress_lines))

        analysis = summary.get("analysis")
        if isinstance(analysis, str) and analysis:
            blocks.append(f"🤖 Анализ\n{analysis}")
        return "\n\n".join(blocks)

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

        if intent == "HEALTH_BIOMETRICS_QUERY":
            normalized = message_text.lower()
            status_terms = (
                "подключ",
                "работает ли",
                "статус",
                "connected",
                "connect",
            )
            if any(term in normalized for term in status_terms):
                status = await HealthTools.get_oura_connection_status(
                    session,
                    user_id=user_id,
                )
                if status["connected"]:
                    return "💍 Oura Ring подключена к вашему личному аккаунту и готова передавать данные."
                return "💍 Oura Ring пока не подключена. Используйте команду /oura в личном чате."

            try:
                summary = await HealthTools.get_oura_daily_summary(
                    session,
                    user_id=user_id,
                    timezone_name=timezone_name,
                )
            except HealthIntegrationError:
                return (
                    "💍 Не удалось получить данные Oura. Если кольцо ещё не подключено "
                    "или доступ был отозван, используйте /oura reconnect в личном чате."
                )

            if all(summary.get(key) is None for key in ("sleep_score", "readiness_score", "activity_score")):
                return (
                    f"💍 Oura подключена, но за {summary['date']} данных пока нет. "
                    "Откройте приложение Oura и синхронизируйте кольцо."
                )
            return cls._format_oura_summary(summary)

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

            expense = cls._extract_expense(message_text)
            if expense is not None:
                from app.agents.finance.agent import FinanceAgent

                amount, merchant = expense
                result = await FinanceAgent().categorize_and_log_transaction(
                    session=session,
                    owner_type="household",
                    owner_id=household_id,
                    amount=amount,
                    merchant=merchant,
                    description=message_text,
                )
                if result.get("status") == "SUCCESS":
                    return (
                        f"💳 Записал расход: **{result['amount']} {result['currency']}** — "
                        f"**{result['merchant']}** ({result['category']}).\n"
                        "Синхронизация с Google Sheets поставлена в очередь."
                    )
                if result.get("status") == "DUPLICATE":
                    return "💳 Этот расход уже был записан ранее."

            if "таблиц" in message_text.lower() or "google sheet" in message_text.lower():
                sync = await FinanceTools.get_latest_sheet_sync_status(
                    session,
                    owner_id=household_id,
                )
                if sync is None:
                    return "💳 Пока нет расходов для синхронизации с Google Sheets."
                status_text = {
                    "synced": "уже добавлена в Google Sheets",
                    "pending": "ожидает синхронизации с Google Sheets",
                    "syncing": "сейчас синхронизируется с Google Sheets",
                    "failed": "пока не синхронизировалась; бот повторит попытку автоматически",
                    "disabled": "создана до включения автоматической синхронизации",
                }.get(sync["status"], "имеет неизвестный статус синхронизации")
                return (
                    f"💳 Последняя запись: **{sync['amount']} {sync['currency']}** — "
                    f"**{sync['merchant']}**; {status_text}."
                )

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

        return await cls._generate_general_response(message_text, user_name)
