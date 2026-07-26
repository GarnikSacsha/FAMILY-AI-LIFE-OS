import uuid
from unittest.mock import AsyncMock, Mock

import pytest

from app.config.settings import settings
from app.domains.identity.models import User
from app.domains.identity.service import IdentityService, PermissionDeniedError


def session_returning(user):
    result = Mock()
    result.scalar_one_or_none.return_value = user
    session = Mock()
    session.execute = AsyncMock(return_value=result)
    return session


def user_for(telegram_id, *, household_id=None):
    return User(
        id=uuid.uuid4(),
        household_id=household_id,
        telegram_id=telegram_id,
        first_name="Family member",
        is_admin=False,
    )


@pytest.fixture
def configured_identity(monkeypatch):
    monkeypatch.setattr(settings, "DENYS_TELEGRAM_ID", 1001)
    monkeypatch.setattr(settings, "OLEKSANDRA_TELEGRAM_ID", 1002)
    monkeypatch.setattr(settings, "FAMILY_GROUP_CHAT_ID", -100500)


@pytest.mark.asyncio
async def test_private_actor_resolves_to_internal_uuids(configured_identity):
    household_id = uuid.uuid4()
    user = user_for(1001, household_id=household_id)
    session = session_returning(user)

    actor = await IdentityService.resolve_actor(
        session,
        telegram_user_id=1001,
        chat_id=1001,
        chat_type="private",
    )

    assert actor.user_id == user.id
    assert actor.household_id == household_id
    assert isinstance(actor.user_id, uuid.UUID)
    assert actor.telegram_id == 1001


@pytest.mark.asyncio
async def test_unknown_telegram_id_is_rejected_before_database_lookup(
    configured_identity,
):
    session = session_returning(None)

    with pytest.raises(PermissionDeniedError, match="Access denied"):
        await IdentityService.resolve_actor(
            session,
            telegram_user_id=9999,
            chat_id=9999,
            chat_type="private",
        )

    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_registered_user_without_household_is_rejected(configured_identity):
    session = session_returning(user_for(1001, household_id=None))

    with pytest.raises(PermissionDeniedError, match="Access denied"):
        await IdentityService.resolve_actor(
            session,
            telegram_user_id=1001,
            chat_id=1001,
            chat_type="private",
        )


@pytest.mark.asyncio
async def test_only_configured_family_group_is_allowed(configured_identity):
    household_id = uuid.uuid4()
    user = user_for(1001, household_id=household_id)

    actor = await IdentityService.resolve_actor(
        session_returning(user),
        telegram_user_id=1001,
        chat_id=-100500,
        chat_type="supergroup",
    )
    assert actor.household_id == household_id

    with pytest.raises(PermissionDeniedError):
        await IdentityService.resolve_actor(
            session_returning(user),
            telegram_user_id=1001,
            chat_id=-100501,
            chat_type="group",
        )


@pytest.mark.asyncio
async def test_groups_are_disabled_when_group_id_is_not_configured(configured_identity, monkeypatch):
    monkeypatch.setattr(settings, "FAMILY_GROUP_CHAT_ID", None)
    user = user_for(1001, household_id=uuid.uuid4())

    with pytest.raises(PermissionDeniedError):
        await IdentityService.resolve_actor(
            session_returning(user),
            telegram_user_id=1001,
            chat_id=-100500,
            chat_type="group",
        )


def test_sensitive_domains_remain_private_only():
    actor = Mock(chat_type="group")

    for domain in ("health", "oauth", "medical_docs", "personal_memory"):
        with pytest.raises(PermissionDeniedError):
            IdentityService.validate_domain_access(actor, domain)
