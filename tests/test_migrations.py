import os

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from alembic import command
from app.domains.documents.models import Document
from app.domains.health import models as health_models
from app.domains.memory.models import PendingConfirmation
from app.infrastructure.database.base import Base

_HEALTH_MODELS = health_models


def test_production_health_revision_is_resolvable():
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    revision = script.get_revision("015_health_data_foundation")

    assert revision is not None


def test_health_migration_schema_is_registered_in_metadata():
    expected_tables = {
        "health_provider_connections",
        "health_source_records",
        "health_observations",
        "health_metric_baselines",
        "health_ai_insights",
        "health_ai_insight_evidence",
    }
    assert expected_tables <= set(Base.metadata.tables)

    document_constraints = {constraint.name for constraint in Document.__table__.constraints}
    assert "uq_documents_owner_id_id" in document_constraints

    confirmation_type = next(
        constraint
        for constraint in PendingConfirmation.__table__.constraints
        if constraint.name == "ck_pending_confirmations_type"
    )
    assert "health_history_delete" in str(confirmation_type.sqltext)


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires disposable PostgreSQL")
def test_migrations_upgrade_downgrade_upgrade(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.check(config)
