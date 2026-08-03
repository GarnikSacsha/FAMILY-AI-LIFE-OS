"""Allow pending recurring calendar actions.

Revision ID: 008_recurring_calendar_pending_actions
Revises: 007_clean_legacy_shared_memory
"""

from collections.abc import Sequence

from alembic import op

revision: str = "008_recurring_calendar_pending_actions"
down_revision: str | None = "007_clean_legacy_shared_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_pending_shared_actions_type",
        "pending_shared_actions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_pending_shared_actions_type",
        "pending_shared_actions",
        "action_type IN ('reminder', 'calendar_recurring')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_pending_shared_actions_type",
        "pending_shared_actions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_pending_shared_actions_type",
        "pending_shared_actions",
        "action_type IN ('reminder')",
    )
