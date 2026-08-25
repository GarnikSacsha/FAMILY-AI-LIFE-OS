import os

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from alembic import command


def test_production_health_revision_is_resolvable():
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    revision = script.get_revision("015_health_data_foundation")

    assert revision is not None


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires disposable PostgreSQL")
def test_migrations_upgrade_downgrade_upgrade(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.check(config)
