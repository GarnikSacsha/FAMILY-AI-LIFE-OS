"""Exercise migrations only against the isolated validation PostgreSQL."""

import asyncio
import json
import os
from urllib.parse import urlparse

database_url = os.environ.get("DATABASE_URL", "")
parsed = urlparse(database_url)
if (
    os.environ.get("ENVIRONMENT") != "test"
    or parsed.scheme != "postgresql+asyncpg"
    or parsed.hostname != "validation-pg"
    or parsed.path != "/family_validation_20261109"
    or parsed.username != "family_validation"
):
    raise SystemExit("Refusing to touch a database outside the dedicated validation container.")

from alembic.config import Config  # noqa: E402
from sqlalchemy import func, inspect, select  # noqa: E402

from alembic import command  # noqa: E402
from app.domains.identity.models import Household, User  # noqa: E402
from app.infrastructure.database.prepare import _ensure_family_identities  # noqa: E402
from app.infrastructure.database.session import AsyncSessionLocal, engine  # noqa: E402


async def tables() -> list[str]:
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(lambda sync: sorted(inspect(sync).get_table_names()))
    finally:
        await engine.dispose()


async def bootstrap_twice() -> dict:
    try:
        async with AsyncSessionLocal.begin() as session:
            first = await _ensure_family_identities(session)
        async with AsyncSessionLocal.begin() as session:
            second = await _ensure_family_identities(session)
        async with AsyncSessionLocal() as session:
            users = await session.scalar(select(func.count()).select_from(User))
            households = await session.scalar(select(func.count()).select_from(Household))
        assert (first, second, users, households) == (2, 0, 2, 1)
        return {"first_created": first, "second_created": second, "users": users, "households": households}
    finally:
        await engine.dispose()


def run() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    first_tables = asyncio.run(tables())
    command.check(config)
    command.downgrade(config, "base")
    remaining = asyncio.run(tables())
    assert set(remaining) <= {"alembic_version"}, remaining
    command.upgrade(config, "head")
    second_tables = asyncio.run(tables())
    assert first_tables == second_tables, {"before": first_tables, "after": second_tables}
    command.check(config)
    bootstrap = asyncio.run(bootstrap_twice())
    print(
        json.dumps(
            {
                "upgrade_downgrade_upgrade": "passed",
                "alembic_check": "passed",
                "table_count": len(second_tables),
                "bootstrap_idempotence": bootstrap,
            }
        )
    )


if __name__ == "__main__":
    run()
