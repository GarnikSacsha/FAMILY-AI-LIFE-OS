import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.domains.finance.models import FinancialTransaction
from app.infrastructure.database.session import AsyncSessionLocal
from app.integrations.google.sheets import GoogleSheetsClient

logger = logging.getLogger(__name__)

GOOGLE_SHEETS_POLL_SECONDS = 5.0
GOOGLE_SHEETS_MAX_ATTEMPTS = 10


@dataclass(frozen=True)
class TransactionSyncItem:
    id: uuid.UUID
    occurred_at: datetime
    merchant: str
    category: str
    amount: str
    currency: str
    direction: str
    source: str
    owner_type: str

    def as_sheet_row(self) -> list[str]:
        return [
            self.occurred_at.astimezone(timezone.utc).isoformat(),
            self.merchant,
            self.category,
            self.amount,
            self.currency,
            self.direction,
            self.source,
            self.owner_type,
            str(self.id),
        ]


async def _claim_next_transaction() -> TransactionSyncItem | None:
    async with AsyncSessionLocal.begin() as session:
        result = await session.execute(
            select(FinancialTransaction)
            .where(
                FinancialTransaction.sheets_sync_status.in_(("pending", "failed", "syncing")),
                FinancialTransaction.sheets_sync_attempts < GOOGLE_SHEETS_MAX_ATTEMPTS,
            )
            .order_by(FinancialTransaction.created_at, FinancialTransaction.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        transaction = result.scalar_one_or_none()
        if transaction is None:
            return None
        transaction.sheets_sync_status = "syncing"
        transaction.sheets_sync_attempts += 1
        transaction.sheets_sync_error = None
        return TransactionSyncItem(
            id=transaction.id,
            occurred_at=transaction.occurred_at,
            merchant=transaction.merchant,
            category=transaction.category,
            amount=str(transaction.amount),
            currency=transaction.currency,
            direction=transaction.direction,
            source=transaction.source,
            owner_type=transaction.owner_type,
        )


async def _mark_synced(transaction_id: uuid.UUID, updated_range: str) -> None:
    async with AsyncSessionLocal.begin() as session:
        await session.execute(
            update(FinancialTransaction)
            .where(FinancialTransaction.id == transaction_id)
            .values(
                sheets_sync_status="synced",
                sheets_synced_at=datetime.now(timezone.utc),
                sheets_sync_error=None,
                sheets_updated_range=updated_range[:255],
            )
        )


async def _mark_failed(transaction_id: uuid.UUID, error: Exception) -> None:
    async with AsyncSessionLocal.begin() as session:
        await session.execute(
            update(FinancialTransaction)
            .where(FinancialTransaction.id == transaction_id)
            .values(
                sheets_sync_status="failed",
                sheets_sync_error=type(error).__name__[:100],
            )
        )


async def run_google_sheets_worker() -> None:
    """Synchronize committed finance rows without coupling Google to DB commits."""
    while True:
        if not GoogleSheetsClient.is_configured():
            await asyncio.sleep(60)
            continue
        item = await _claim_next_transaction()
        if item is None:
            await asyncio.sleep(GOOGLE_SHEETS_POLL_SECONDS)
            continue
        try:
            updated_range = await GoogleSheetsClient.append_transaction(item.as_sheet_row())
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "Google Sheets synchronization failed (%s).",
                type(error).__name__,
            )
            await _mark_failed(item.id, error)
        else:
            await _mark_synced(item.id, updated_range)
