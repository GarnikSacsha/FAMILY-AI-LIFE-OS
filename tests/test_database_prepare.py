import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.settings import settings
from app.domains.identity.models import Household, User
from app.infrastructure.database.prepare import _ensure_family_identities


def _result_with_users(users):
    result = MagicMock()
    result.scalars.return_value.all.return_value = users
    return result


@pytest.mark.asyncio
async def test_family_identity_bootstrap_creates_household_and_users(monkeypatch):
    monkeypatch.setattr(settings, "DENYS_TELEGRAM_ID", 1001)
    monkeypatch.setattr(settings, "OLEKSANDRA_TELEGRAM_ID", 1002)

    users_result = _result_with_users([])
    household_result = MagicMock()
    household_result.scalar_one_or_none.return_value = None

    session = MagicMock()
    session.execute = AsyncMock(side_effect=[users_result, household_result])
    added = []
    session.add.side_effect = added.append

    async def flush():
        household = next(item for item in added if isinstance(item, Household))
        household.id = uuid.uuid4()

    session.flush = AsyncMock(side_effect=flush)

    created = await _ensure_family_identities(session)

    assert created == 2
    household = next(item for item in added if isinstance(item, Household))
    users = [item for item in added if isinstance(item, User)]
    assert {user.telegram_id for user in users} == {1001, 1002}
    assert all(user.household_id == household.id for user in users)
    assert next(user for user in users if user.telegram_id == 1001).is_admin


@pytest.mark.asyncio
async def test_family_identity_bootstrap_is_idempotent(monkeypatch):
    monkeypatch.setattr(settings, "DENYS_TELEGRAM_ID", 1001)
    monkeypatch.setattr(settings, "OLEKSANDRA_TELEGRAM_ID", 1002)
    household_id = uuid.uuid4()
    users = [
        User(
            household_id=household_id,
            telegram_id=1001,
            first_name="Denys",
            is_admin=False,
        ),
        User(
            household_id=household_id,
            telegram_id=1002,
            first_name="Oleksandra",
            is_admin=False,
        ),
    ]

    session = MagicMock()
    session.execute = AsyncMock(return_value=_result_with_users(users))

    created = await _ensure_family_identities(session)

    assert created == 0
    session.add.assert_not_called()
    assert users[0].is_admin
