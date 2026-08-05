import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from sqlalchemy import and_, or_, select, update

from app.config.settings import settings
from app.domains.finance.models import FinancialTransaction
from app.infrastructure.database.session import AsyncSessionLocal
from app.integrations.google.sheets import GoogleSheetsClient

logger = logging.getLogger(__name__)

GOOGLE_SHEETS_POLL_SECONDS = 5.0
GOOGLE_SHEETS_MAX_ATTEMPTS = 10
GOOGLE_SHEETS_LEASE_MARGIN_SECONDS = 30
KYIV_TIMEZONE = ZoneInfo("Europe/Kyiv")


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
    telegram_chat_id: int | None = None

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
    current = datetime.now(timezone.utc)
    lease_seconds = max(
        120,
        int(settings.GOOGLE_SHEETS_OPERATION_TIMEOUT_SECONDS) + GOOGLE_SHEETS_LEASE_MARGIN_SECONDS,
    )
    stale_before = current - timedelta(seconds=lease_seconds)
    projection_key = GoogleSheetsClient.projection_key()
    state_is_retryable = or_(
        FinancialTransaction.sheets_sync_status == "pending",
        and_(
            FinancialTransaction.sheets_sync_status == "failed",
            or_(
                FinancialTransaction.sheets_next_attempt_at.is_(None),
                FinancialTransaction.sheets_next_attempt_at <= current,
            ),
        ),
        and_(
            FinancialTransaction.sheets_sync_status == "syncing",
            FinancialTransaction.sheets_sync_started_at <= stale_before,
        ),
    )
    target_mismatch = or_(
        FinancialTransaction.sheets_projection_key.is_(None),
        FinancialTransaction.sheets_projection_key != projection_key,
    )
    conditions = [state_is_retryable, FinancialTransaction.sheets_sync_attempts < GOOGLE_SHEETS_MAX_ATTEMPTS]
    if settings.GOOGLE_SHEETS_LAYOUT == "monthly_budget":
        period_start, period_end = _monthly_period_bounds()
        conditions = [
            or_(
                state_is_retryable,
                and_(
                    FinancialTransaction.sheets_sync_status == "synced",
                    target_mismatch,
                ),
            ),
            or_(
                FinancialTransaction.sheets_sync_attempts < GOOGLE_SHEETS_MAX_ATTEMPTS,
                target_mismatch,
            ),
            FinancialTransaction.direction == "expense",
            FinancialTransaction.currency == "UAH",
            FinancialTransaction.occurred_at >= period_start,
            FinancialTransaction.occurred_at < period_end,
        ]
    async with AsyncSessionLocal.begin() as session:
        result = await session.execute(
            select(FinancialTransaction)
            .where(*conditions)
            .order_by(FinancialTransaction.created_at, FinancialTransaction.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        transaction = result.scalar_one_or_none()
        if transaction is None:
            return None
        if settings.GOOGLE_SHEETS_LAYOUT == "monthly_budget" and transaction.sheets_projection_key != projection_key:
            transaction.sheets_sync_attempts = 0
        transaction.sheets_sync_status = "syncing"
        transaction.sheets_sync_attempts += 1
        transaction.sheets_sync_started_at = current
        transaction.sheets_next_attempt_at = None
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
            telegram_chat_id=getattr(transaction, "telegram_chat_id", None),
        )


def _monthly_period_bounds() -> tuple[datetime, datetime]:
    period = settings.GOOGLE_SHEETS_PERIOD
    if period is None:
        raise ValueError("GOOGLE_SHEETS_PERIOD is required for the monthly_budget layout.")
    year, month = (int(part) for part in period.split("-", maxsplit=1))
    period_start = datetime(year, month, 1, tzinfo=KYIV_TIMEZONE)
    if month == 12:
        period_end = datetime(year + 1, 1, 1, tzinfo=KYIV_TIMEZONE)
    else:
        period_end = datetime(year, month + 1, 1, tzinfo=KYIV_TIMEZONE)
    return period_start.astimezone(timezone.utc), period_end.astimezone(timezone.utc)


async def _mark_synced(transaction_id: uuid.UUID, updated_range: str, *, projection_key: str | None = None) -> None:
    values: dict[str, object] = {
        "sheets_sync_status": "synced",
        "sheets_synced_at": datetime.now(timezone.utc),
        "sheets_sync_started_at": None,
        "sheets_next_attempt_at": None,
        "sheets_sync_error": None,
        "sheets_updated_range": updated_range[:255],
    }
    if projection_key is not None:
        values["sheets_projection_key"] = projection_key[:255]
    async with AsyncSessionLocal.begin() as session:
        await session.execute(
            update(FinancialTransaction).where(FinancialTransaction.id == transaction_id).values(**values)
        )


def _retry_delay(attempt: int) -> timedelta:
    return timedelta(seconds=min(5 * (2 ** max(0, attempt - 1)), 15 * 60))


async def _mark_failed(transaction_id: uuid.UUID, error: Exception) -> tuple[int | None, bool]:
    async with AsyncSessionLocal.begin() as session:
        result = await session.execute(
            select(FinancialTransaction).where(FinancialTransaction.id == transaction_id).with_for_update()
        )
        transaction = result.scalar_one_or_none()
        if transaction is None:
            return None, False
        current = datetime.now(timezone.utc)
        transaction.sheets_sync_status = "failed"
        transaction.sheets_sync_error = type(error).__name__[:100]
        transaction.sheets_sync_started_at = None
        transaction.sheets_next_attempt_at = current + _retry_delay(transaction.sheets_sync_attempts)
        telegram_chat_id = getattr(transaction, "telegram_chat_id", None)
        notify = telegram_chat_id is not None and getattr(transaction, "sheets_failure_notified_at", None) is None
        return telegram_chat_id, notify


async def _mark_failure_notification_sent(transaction_id: uuid.UUID) -> None:
    async with AsyncSessionLocal.begin() as session:
        await session.execute(
            update(FinancialTransaction)
            .where(
                FinancialTransaction.id == transaction_id,
                FinancialTransaction.sheets_failure_notified_at.is_(None),
            )
            .values(sheets_failure_notified_at=datetime.now(timezone.utc))
        )


async def _notify_failure(bot_instance: Bot, telegram_chat_id: int) -> bool:
    try:
        await bot_instance.send_message(
            chat_id=telegram_chat_id,
            text=(
                "Google Sheets сейчас недоступны. Расход уже сохранён в системе; синхронизацию повторю автоматически."
            ),
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.warning("Google Sheets failure notification failed (%s).", type(error).__name__)
        return False
    return True


async def run_google_sheets_worker(bot_instance: Bot | None = None) -> None:
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
            projection_key = GoogleSheetsClient.projection_key()
            if settings.GOOGLE_SHEETS_LAYOUT == "monthly_budget":
                updated_range = await GoogleSheetsClient.project_monthly_budget_expense(
                    transaction_id=str(item.id),
                    occurred_at=item.occurred_at,
                    category=item.category,
                    amount=item.amount,
                )
            else:
                updated_range = await GoogleSheetsClient.append_transaction(item.as_sheet_row())
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "Google Sheets synchronization failed (%s).",
                type(error).__name__,
            )
            failure = await _mark_failed(item.id, error)
            telegram_chat_id, notify = failure if isinstance(failure, tuple) else (None, False)
            if bot_instance is not None and notify and telegram_chat_id is not None:
                if await _notify_failure(bot_instance, telegram_chat_id):
                    await _mark_failure_notification_sent(item.id)
        else:
            if settings.GOOGLE_SHEETS_LAYOUT == "monthly_budget":
                await _mark_synced(item.id, updated_range, projection_key=projection_key)
            else:
                await _mark_synced(item.id, updated_range)
