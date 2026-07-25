import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.finance.models import FinancialTransaction


class FinanceTools:
    """Deterministic finance tools enforcing Decimal precision, date boundaries, and multi-currency aggregation."""

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
        """Logs a financial transaction with composite deduplication and Decimal precision."""
        if amount <= 0:
            raise ValueError("Transaction amount must be strictly greater than zero.")

        if direction not in ("expense", "income"):
            raise ValueError("Direction must be either 'expense' or 'income'.")

        decimal_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
        currency_code = currency.upper().strip()

        if external_id:
            stmt = select(FinancialTransaction).where(
                FinancialTransaction.external_id == external_id,
                FinancialTransaction.owner_id == owner_id,
                FinancialTransaction.source == source,
            )
            existing = await session.execute(stmt)
            if existing.scalar_one_or_none():
                return {"status": "DUPLICATE", "message": "Transaction already exists."}

        tx = FinancialTransaction(
            owner_type=owner_type,
            owner_id=owner_id,
            occurred_at=datetime.now(timezone.utc),
            amount=decimal_amount,
            currency=currency_code,
            merchant=merchant.strip(),
            category=category.strip(),
            description=description,
            direction=direction,
            source=source,
            external_id=external_id,
        )

        session.add(tx)
        await session.flush()

        return {
            "transaction_id": str(tx.id),
            "amount": str(tx.amount),
            "currency": tx.currency,
            "merchant": tx.merchant,
            "category": tx.category,
            "status": "SUCCESS",
        }

    @staticmethod
    async def get_spending_summary(
        session: AsyncSession,
        owner_id: uuid.UUID,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        currency: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieves total expenses aggregated per currency over a half-open date interval [date_from, date_to)."""
        conditions = [
            FinancialTransaction.owner_id == owner_id,
            FinancialTransaction.direction == "expense",
        ]

        if date_from:
            conditions.append(FinancialTransaction.occurred_at >= date_from)
        if date_to:
            conditions.append(FinancialTransaction.occurred_at < date_to)
        if currency:
            conditions.append(FinancialTransaction.currency == currency.upper().strip())

        stmt = (
            select(
                FinancialTransaction.currency,
                FinancialTransaction.category,
                func.sum(FinancialTransaction.amount).label("total_amount"),
            )
            .where(and_(*conditions))
            .group_by(FinancialTransaction.currency, FinancialTransaction.category)
        )
        result = await session.execute(stmt)

        summary_by_currency: Dict[str, Dict[str, Any]] = {}
        for row in result.all():
            curr = row.currency
            cat = row.category
            amount = Decimal(str(row.total_amount)).quantize(Decimal("0.01"))

            if curr not in summary_by_currency:
                summary_by_currency[curr] = {"total_expense": Decimal("0.00"), "categories": {}}

            summary_by_currency[curr]["total_expense"] += amount
            summary_by_currency[curr]["categories"][cat] = str(amount)

        # Convert Decimals to string representation for clean serialization
        for curr in summary_by_currency:
            summary_by_currency[curr]["total_expense"] = str(summary_by_currency[curr]["total_expense"])

        return {
            "currencies": summary_by_currency,
        }
