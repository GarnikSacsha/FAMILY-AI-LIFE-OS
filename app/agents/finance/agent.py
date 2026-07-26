import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.llm.provider import GeminiFinanceProvider
from app.tools.finance_tools import FinanceTools


class CategorySchema(BaseModel):
    category: str = Field(description="Expense category e.g. Restaurants, Groceries, Transport, Health, Utilities")
    subcategory: str | None = Field(description="Specific subcategory")
    confidence: float = Field(description="Confidence rating from 0.0 to 1.0")


class FinanceAgent:
    """Finance Agent powered exclusively by Gemini for financial intelligence & Google Workspace integration."""

    def __init__(self):
        self.provider = GeminiFinanceProvider()

    async def categorize_and_log_transaction(
        self,
        session: AsyncSession,
        owner_type: str,
        owner_id: uuid.UUID,
        amount: float,
        merchant: str,
        description: str = "",
    ) -> dict[str, Any]:
        """Uses Gemini to intelligently categorize an expense, then logs it via FinanceTools."""
        prompt = (
            f"Categorize this transaction:\n"
            f"Merchant: {merchant}\n"
            f"Amount: {amount} UAH\n"
            f"Description: {description}\n\n"
            f"Return the exact category and subcategory."
        )

        try:
            categorization = await self.provider.generate_structured_json(prompt, CategorySchema)
            category = categorization.get("category", "Uncategorized")
        except Exception:
            category = "Uncategorized"

        result = await FinanceTools.log_transaction(
            session=session,
            owner_type=owner_type,
            owner_id=owner_id,
            amount=amount,
            merchant=merchant,
            category=category,
            description=description,
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
