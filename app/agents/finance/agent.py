import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.llm.provider import GeminiFinanceProvider
from app.tools.finance_tools import FinanceTools


class CategorySchema(BaseModel):
    category: str = Field(
        description="Expense category e.g. Restaurants, Groceries, Transport, Health, Utilities, Sports"
    )
    subcategory: str | None = Field(description="Specific subcategory")
    confidence: float = Field(description="Confidence rating from 0.0 to 1.0")


class FinanceAgent:
    """Finance Agent powered exclusively by Gemini for financial intelligence & Google Workspace integration."""

    def __init__(self):
        self.provider = GeminiFinanceProvider()

    @staticmethod
    def _fallback_category(description: str, merchant: str) -> str:
        normalized = f"{merchant} {description}".lower()
        rules = (
            ("Pets", ("корм", "ветеринар", "зоомагаз", "кот", "собак")),
            ("Entertainment", ("відпочинок", "отдых", "кино", "ігр", "игр", "развлеч", "концерт")),
            ("Groceries", ("продукт", "супермаркет", "ринок", "рынок", "базар", "їжа", "еда")),
            ("Restaurants", ("кафе", "ресторан", "доставка", "кофе", "пицц")),
            ("Transport", ("транспорт", "таксі", "такси", "бензин", "паливо", "топливо", "проїзд", "проезд", "парков")),
            ("Health", ("здоров", "аптек", "лікар", "врач", "лекар", "аналіз", "анализ")),
            ("Utilities", ("рахунк", "коммун", "комун", "моб", "світ", "свет", "газ", "вод", "інтернет", "интернет")),
            ("Sports", ("спорт", "тренув", "тренир", "фітнес", "фитнес", "gym", "зал")),
        )
        for category, terms in rules:
            if any(term in normalized for term in terms):
                return category
        return "Uncategorized"

    async def categorize_and_log_transaction(
        self,
        session: AsyncSession,
        owner_type: str,
        owner_id: uuid.UUID,
        amount: float,
        merchant: str,
        description: str = "",
        external_id: str | None = None,
        telegram_chat_id: int | None = None,
    ) -> dict[str, Any]:
        """Uses Gemini to intelligently categorize an expense, then logs it via FinanceTools."""
        prompt = (
            f"Categorize this transaction:\n"
            f"Merchant: {merchant}\n"
            f"Amount: {amount} UAH\n"
            f"Description: {description}\n\n"
            f"Return the exact category and subcategory."
        )

        fallback_category = self._fallback_category(description, merchant)
        allowed_categories = {
            "Entertainment",
            "Groceries",
            "Health",
            "Pets",
            "Restaurants",
            "Shopping",
            "Sports",
            "Transport",
            "Utilities",
        }
        try:
            categorization = await self.provider.generate_structured_json(prompt, CategorySchema)
            proposed_category = str(categorization.get("category", "")).strip()
            category = proposed_category if proposed_category in allowed_categories else fallback_category
        except Exception:
            category = fallback_category

        result = await FinanceTools.log_transaction(
            session=session,
            owner_type=owner_type,
            owner_id=owner_id,
            amount=amount,
            merchant=merchant,
            category=category,
            description=description,
            external_id=external_id,
            telegram_chat_id=telegram_chat_id,
        )

        return result

    async def generate_financial_report(self, session: AsyncSession, household_id: uuid.UUID) -> str:
        """Uses Gemini to generate an empathetic, structured financial report for the household."""
        summary = await FinanceTools.get_spending_summary(session, owner_id=household_id)

        prompt = (
            f"You are the Finance Agent for Denys & Oleksandra.\n"
            f"Here is the household spending data for this month: {summary}\n\n"
            f"Generate a clear, helpful financial summary in Russian with key takeaways and advice."
        )

        return await self.provider.generate_text(prompt)
