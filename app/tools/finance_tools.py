import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.finance.models import FinancialTransaction


class FinanceTools:
    """Deterministic finance tools for database transactions & budgeting."""

    @staticmethod
    async def log_transaction(
        session: AsyncSession,
        owner_type: str,
        owner_id: uuid.UUID,
        amount: float,
        merchant: str,
        category: str = "Uncategorized",
        description: Optional[str] = None,
        currency: str = "UAH",
        direction: str = "expense",
        source: str = "manual",
        external_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Logs a financial transaction with deduplication checks."""
        if external_id:
            stmt = select(FinancialTransaction).where(FinancialTransaction.external_id == external_id)
            existing = await session.execute(stmt)
            if existing.scalar_one_or_none():
                return {"status": "DUPLICATE", "message": "Transaction with this external_id already exists."}

        tx = FinancialTransaction(
            owner_type=owner_type,
            owner_id=owner_id,
            occurred_at=datetime.now(timezone.utc),
            amount=amount,
            currency=currency,
            merchant=merchant,
            category=category,
            description=description,
            direction=direction,
            source=source,
            external_id=external_id,
        )

        session.add(tx)
        await session.flush()

        return {
            "transaction_id": str(tx.id),
            "amount": float(tx.amount),
            "currency": tx.currency,
            "merchant": tx.merchant,
            "category": tx.category,
            "status": "SUCCESS",
        }

    @staticmethod
    async def get_spending_summary(
        session: AsyncSession, owner_id: uuid.UUID
    ) -> Dict[str, Any]:
        """Retrieves total expenses and category breakdown."""
        stmt = (
            select(
                FinancialTransaction.category,
                func.sum(FinancialTransaction.amount).label("total_amount"),
            )
            .where(
                FinancialTransaction.owner_id == owner_id,
                FinancialTransaction.direction == "expense",
            )
            .group_by(FinancialTransaction.category)
        )
        result = await session.execute(stmt)
        categories = {row.category: float(row.total_amount) for row in result.all()}
        total_expense = sum(categories.values())

        return {
            "total_expense": total_expense,
            "currency": "UAH",
            "categories": categories,
        }
