import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.finance.models import FinancialTransaction

MONEY_QUANTUM = Decimal("0.01")
ALLOWED_OWNER_TYPES = frozenset({"user", "household"})
ALLOWED_DIRECTIONS = frozenset({"expense", "income"})


def _normalize_required(value: str, *, field: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty.")
    if len(normalized) > max_length:
        raise ValueError(f"{field} exceeds the maximum length of {max_length}.")
    return normalized


def _normalize_currency(value: str) -> str:
    currency = value.strip().upper()
    if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
        raise ValueError("Currency must be a three-letter ISO 4217 code.")
    return currency


def _normalize_amount(value: Decimal | str | int | float) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Transaction amount must be a finite decimal number.") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Transaction amount must be strictly greater than zero.")
    if amount >= Decimal("10000000000"):
        raise ValueError("Transaction amount exceeds the supported database range.")
    return amount


class FinanceTools:
    """Deterministic finance tools with database-backed import idempotency."""

    @staticmethod
    async def log_transaction(
        session: AsyncSession,
        owner_type: str,
        owner_id: uuid.UUID,
        amount: Decimal | str | int | float,
        merchant: str,
        category: str = "Uncategorized",
        description: str | None = None,
        currency: str = "UAH",
        direction: str = "expense",
        source: str = "manual",
        external_id: str | None = None,
        account_id: str = "default",
        occurred_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Log a transaction, atomically deduplicating imported records on PostgreSQL."""
        normalized_owner_type = owner_type.strip().lower()
        if normalized_owner_type not in ALLOWED_OWNER_TYPES:
            raise ValueError("Owner type must be either 'user' or 'household'.")
        if not isinstance(owner_id, uuid.UUID):
            raise ValueError("owner_id must be an internal UUID.")

        normalized_direction = direction.strip().lower()
        if normalized_direction not in ALLOWED_DIRECTIONS:
            raise ValueError("Direction must be either 'expense' or 'income'.")

        normalized_amount = _normalize_amount(amount)
        normalized_merchant = _normalize_required(merchant, field="Merchant", max_length=200)
        normalized_category = _normalize_required(category, field="Category", max_length=100)
        normalized_source = _normalize_required(source, field="Source", max_length=50).lower()
        normalized_account_id = _normalize_required(account_id, field="Account ID", max_length=255)
        normalized_external_id = external_id.strip() if external_id else None
        if normalized_external_id and len(normalized_external_id) > 255:
            raise ValueError("External ID exceeds the maximum length of 255.")
        normalized_description = description.strip() if description else None
        if normalized_description and len(normalized_description) > 500:
            raise ValueError("Description exceeds the maximum length of 500.")

        timestamp = occurred_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware.")
        timestamp = timestamp.astimezone(timezone.utc)

        values = {
            "id": uuid.uuid4(),
            "owner_type": normalized_owner_type,
            "owner_id": owner_id,
            "occurred_at": timestamp,
            "amount": normalized_amount,
            "currency": _normalize_currency(currency),
            "merchant": normalized_merchant,
            "category": normalized_category,
            "description": normalized_description,
            "direction": normalized_direction,
            "source": normalized_source,
            "account_id": normalized_account_id,
            "external_id": normalized_external_id,
        }

        if normalized_external_id and session.get_bind().dialect.name == "postgresql":
            statement = (
                postgresql_insert(FinancialTransaction)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=[
                        FinancialTransaction.source,
                        FinancialTransaction.account_id,
                        FinancialTransaction.owner_type,
                        FinancialTransaction.owner_id,
                        FinancialTransaction.external_id,
                    ],
                    index_where=FinancialTransaction.external_id.is_not(None),
                )
                .returning(
                    FinancialTransaction.id,
                    FinancialTransaction.amount,
                    FinancialTransaction.currency,
                    FinancialTransaction.merchant,
                    FinancialTransaction.category,
                )
            )
            inserted = (await session.execute(statement)).mappings().one_or_none()
            if inserted is None:
                return {"status": "DUPLICATE", "message": "Transaction already exists."}
            return {
                "transaction_id": str(inserted["id"]),
                "amount": str(inserted["amount"]),
                "currency": inserted["currency"],
                "merchant": inserted["merchant"],
                "category": inserted["category"],
                "status": "SUCCESS",
            }

        # SQLite is used only by focused unit tests. Production uses the atomic
        # PostgreSQL branch above and never relies on this pre-insert lookup.
        if normalized_external_id:
            duplicate_query = select(FinancialTransaction.id).where(
                FinancialTransaction.source == normalized_source,
                FinancialTransaction.account_id == normalized_account_id,
                FinancialTransaction.owner_type == normalized_owner_type,
                FinancialTransaction.owner_id == owner_id,
                FinancialTransaction.external_id == normalized_external_id,
            )
            if (await session.execute(duplicate_query)).scalar_one_or_none() is not None:
                return {"status": "DUPLICATE", "message": "Transaction already exists."}

        transaction = FinancialTransaction(**values)
        session.add(transaction)
        await session.flush()
        return {
            "transaction_id": str(transaction.id),
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "merchant": transaction.merchant,
            "category": transaction.category,
            "status": "SUCCESS",
        }

    @staticmethod
    async def get_spending_summary(
        session: AsyncSession,
        owner_id: uuid.UUID,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        currency: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate expenses by currency over the half-open interval [from, to)."""
        if not isinstance(owner_id, uuid.UUID):
            raise ValueError("owner_id must be an internal UUID.")
        if date_from and (date_from.tzinfo is None or date_from.utcoffset() is None):
            raise ValueError("date_from must be timezone-aware.")
        if date_to and (date_to.tzinfo is None or date_to.utcoffset() is None):
            raise ValueError("date_to must be timezone-aware.")
        if date_from and date_to and date_from >= date_to:
            raise ValueError("date_from must be earlier than date_to.")

        conditions = [
            FinancialTransaction.owner_id == owner_id,
            FinancialTransaction.direction == "expense",
        ]
        if date_from:
            conditions.append(FinancialTransaction.occurred_at >= date_from.astimezone(timezone.utc))
        if date_to:
            conditions.append(FinancialTransaction.occurred_at < date_to.astimezone(timezone.utc))
        if currency:
            conditions.append(FinancialTransaction.currency == _normalize_currency(currency))

        statement = (
            select(
                FinancialTransaction.currency,
                FinancialTransaction.category,
                func.sum(FinancialTransaction.amount).label("total_amount"),
            )
            .where(and_(*conditions))
            .group_by(FinancialTransaction.currency, FinancialTransaction.category)
            .order_by(FinancialTransaction.currency, FinancialTransaction.category)
        )
        result = await session.execute(statement)

        summary_by_currency: dict[str, dict[str, Any]] = {}
        for row in result.all():
            amount = Decimal(str(row.total_amount)).quantize(MONEY_QUANTUM)
            currency_summary = summary_by_currency.setdefault(
                row.currency,
                {"total_expense": Decimal("0.00"), "categories": {}},
            )
            currency_summary["total_expense"] += amount
            currency_summary["categories"][row.category] = str(amount)

        for currency_summary in summary_by_currency.values():
            currency_summary["total_expense"] = str(currency_summary["total_expense"].quantize(MONEY_QUANTUM))

        return {"currencies": summary_by_currency}

    @staticmethod
    async def get_latest_sheet_sync_status(
        session: AsyncSession,
        *,
        owner_id: uuid.UUID,
    ) -> dict[str, str] | None:
        if not isinstance(owner_id, uuid.UUID):
            raise ValueError("owner_id must be an internal UUID.")
        result = await session.execute(
            select(FinancialTransaction)
            .where(FinancialTransaction.owner_id == owner_id)
            .order_by(
                FinancialTransaction.occurred_at.desc(),
                FinancialTransaction.created_at.desc(),
                FinancialTransaction.id.desc(),
            )
            .limit(1)
        )
        transaction = result.scalar_one_or_none()
        if transaction is None:
            return None
        return {
            "transaction_id": str(transaction.id),
            "merchant": transaction.merchant,
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "status": transaction.sheets_sync_status,
        }
