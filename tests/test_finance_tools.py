import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domains.finance.models import FinancialTransaction
from app.infrastructure.database.base import Base
from app.tools.finance_tools import FinanceTools


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=[FinancialTransaction.__table__])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as current_session:
        yield current_session
    await engine.dispose()


@pytest.mark.asyncio
async def test_finance_summary_is_decimal_date_bounded_and_currency_separated(session):
    owner_id = uuid.uuid4()
    july = datetime(2026, 7, 15, tzinfo=timezone.utc)
    august = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for amount, currency, occurred_at in [
        ("0.10", "UAH", july),
        ("0.20", "UAH", july),
        ("5.00", "EUR", july),
        ("99.00", "UAH", august),
    ]:
        await FinanceTools.log_transaction(
            session,
            owner_type="household",
            owner_id=owner_id,
            amount=Decimal(amount),
            merchant="Test",
            currency=currency,
            occurred_at=occurred_at,
        )

    summary = await FinanceTools.get_spending_summary(
        session,
        owner_id,
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        august,
    )
    assert summary["currencies"]["UAH"]["total_expense"] == "0.30"
    assert summary["currencies"]["EUR"]["total_expense"] == "5.00"


@pytest.mark.asyncio
async def test_external_id_deduplicates_within_account(session):
    owner_id = uuid.uuid4()
    first = await FinanceTools.log_transaction(
        session,
        owner_type="user",
        owner_id=owner_id,
        amount="10",
        merchant="Test",
        source="bank",
        account_id="account-1",
        external_id="tx-1",
    )
    duplicate = await FinanceTools.log_transaction(
        session,
        owner_type="user",
        owner_id=owner_id,
        amount="10",
        merchant="Test",
        source="bank",
        account_id="account-1",
        external_id="tx-1",
    )
    assert first["status"] == "SUCCESS"
    assert duplicate["status"] == "DUPLICATE"


@pytest.mark.asyncio
async def test_invalid_finance_input_is_rejected(session):
    with pytest.raises(ValueError):
        await FinanceTools.log_transaction(
            session,
            owner_type="invalid",
            owner_id=uuid.uuid4(),
            amount="0",
            merchant="",
            currency="INVALID",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"owner_id": "not-a-uuid"},
        {"direction": "transfer"},
        {"amount": "not-a-number"},
        {"amount": -1},
        {"amount": 10_000_000_000},
        {"merchant": ""},
        {"category": ""},
        {"source": ""},
        {"account_id": ""},
        {"external_id": "x" * 256},
        {"description": "x" * 501},
        {"currency": "US"},
        {"occurred_at": datetime(2026, 8, 5)},
    ],
)
async def test_log_transaction_rejects_each_invalid_normalized_field(session, overrides):
    values = {
        "owner_type": "user",
        "owner_id": uuid.uuid4(),
        "amount": 10,
        "merchant": "Test",
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        await FinanceTools.log_transaction(session, **values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"owner_id": "not-a-uuid"},
        {"date_from": datetime(2026, 8, 5)},
        {"date_to": datetime(2026, 8, 5)},
        {
            "date_from": datetime(2026, 8, 6, tzinfo=timezone.utc),
            "date_to": datetime(2026, 8, 5, tzinfo=timezone.utc),
        },
        {"currency": "US"},
    ],
)
async def test_spending_summary_rejects_invalid_filters(session, kwargs):
    values = {"owner_id": uuid.uuid4()}
    values.update(kwargs)

    with pytest.raises(ValueError):
        await FinanceTools.get_spending_summary(session, **values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"owner_id": "not-a-uuid"},
        {"date_from": datetime(2026, 8, 5)},
        {"date_to": datetime(2026, 8, 6)},
        {"query": "   "},
    ],
)
async def test_find_expenses_rejects_invalid_filters(session, kwargs):
    values = {
        "owner_id": uuid.uuid4(),
        "query": "food",
        "date_from": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "date_to": datetime(2026, 8, 6, tzinfo=timezone.utc),
    }
    values.update(kwargs)

    with pytest.raises(ValueError):
        await FinanceTools.find_expenses(session, **values)


@pytest.mark.asyncio
async def test_latest_sheet_sync_status_is_none_for_unknown_owner(session):
    assert await FinanceTools.get_latest_sheet_sync_status(session, owner_id=uuid.uuid4()) is None
