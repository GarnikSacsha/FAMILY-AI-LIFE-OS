"""Remove redundant identity unique constraints.

Revision ID: 003_identity_unique_cleanup
Revises: 002_security_hardening
"""

from collections.abc import Sequence

from alembic import op

revision: str = "003_identity_unique_cleanup"
down_revision: str | None = "002_security_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Both columns already have unique indexes. The initial migration also
    # created equivalent constraints, which duplicated enforcement and made
    # the live schema diverge from SQLAlchemy metadata.
    op.drop_constraint(
        "oauth_authorization_states_state_hash_key",
        "oauth_authorization_states",
        type_="unique",
    )
    op.drop_constraint("users_telegram_id_key", "users", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint("users_telegram_id_key", "users", ["telegram_id"])
    op.create_unique_constraint(
        "oauth_authorization_states_state_hash_key",
        "oauth_authorization_states",
        ["state_hash"],
    )
