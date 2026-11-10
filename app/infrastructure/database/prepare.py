import asyncio
import logging

from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from app.config.settings import settings
from app.domains.identity.models import Household, User
from app.infrastructure.database.session import AsyncSessionLocal, engine

logger = logging.getLogger(__name__)


async def _ensure_family_identities(session: AsyncSession) -> int:
    identities = (
        (settings.DENYS_TELEGRAM_ID, "Denys", True),
        (settings.OLEKSANDRA_TELEGRAM_ID, "Oleksandra", False),
    )
    telegram_ids = [telegram_id for telegram_id, _, _ in identities]

    users_result = await session.execute(select(User).where(User.telegram_id.in_(telegram_ids)))
    users_by_telegram_id = {user.telegram_id: user for user in users_result.scalars().all()}

    household_id = next(
        (user.household_id for user in users_by_telegram_id.values() if user.household_id is not None),
        None,
    )
    if household_id is None:
        household_result = await session.execute(
            select(Household).order_by(Household.created_at, Household.id).limit(1)
        )
        household = household_result.scalar_one_or_none()
        if household is None:
            household = Household(
                name="Family Workspace",
                description="Provisioned family workspace",
                timezone="Europe/Kyiv",
            )
            session.add(household)
            await session.flush()
        household_id = household.id

    created = 0
    for telegram_id, first_name, is_admin in identities:
        user = users_by_telegram_id.get(telegram_id)
        if user is None:
            session.add(
                User(
                    household_id=household_id,
                    telegram_id=telegram_id,
                    first_name=first_name,
                    timezone="Europe/Kyiv",
                    language="ru",
                    is_admin=is_admin,
                )
            )
            created += 1
            continue

        if user.household_id is None:
            user.household_id = household_id
        if is_admin:
            user.is_admin = True

    return created


async def bootstrap_family_identities() -> None:
    async with AsyncSessionLocal.begin() as session:
        created = await _ensure_family_identities(session)
    logger.info("Family identity bootstrap complete (created=%d).", created)


def run_migrations() -> None:
    command.upgrade(Config("alembic.ini"), "head")


async def prepare_database() -> None:
    try:
        await bootstrap_family_identities()
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    run_migrations()
    asyncio.run(prepare_database())


if __name__ == "__main__":
    main()
