import os

import pytest
from alembic.config import Config

from alembic import command


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires disposable PostgreSQL")
def test_migrations_upgrade_downgrade_upgrade(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.check(config)
