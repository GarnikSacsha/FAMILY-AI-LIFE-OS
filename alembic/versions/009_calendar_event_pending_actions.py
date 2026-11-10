"""Allow pending one-off calendar actions.

Revision ID: 009_calendar_event_pending
Revises: 008_recurring_calendar
"""

from collections.abc import Sequence

from alembic import op

revision: str = "009_calendar_event_pending"
down_revision: str | None = "008_recurring_calendar"
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
        "action_type IN ('reminder', 'calendar_recurring', 'calendar_event')",
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
        "action_type IN ('reminder', 'calendar_recurring')",
    )
