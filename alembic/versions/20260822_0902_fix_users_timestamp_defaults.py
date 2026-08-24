"""fix missing server defaults on users.created_at/updated_at

Revision ID: 20260822_0902
Revises: 20260822_0901
Create Date: 2026-08-22 09:02:00.000000

The `users` table was created by 20260821_1330_split_users_from_accounts
without a DB-level default on created_at/updated_at, even though
app/accounts/models.py declares server_default=func.now() -- the ORM
therefore omits both columns from INSERT statements (trusting the server
to fill them in), and Postgres has no default to apply, so every new
signup fails with a NotNullViolationError. Backfilled rows from that
migration's INSERT...SELECT already have real timestamps; this only adds
the missing defaults going forward.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_0902"
down_revision: Union[str, None] = "20260822_0901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("users", "created_at", server_default=sa.text("now()"))
    op.alter_column("users", "updated_at", server_default=sa.text("now()"))


def downgrade() -> None:
    op.alter_column("users", "created_at", server_default=None)
    op.alter_column("users", "updated_at", server_default=None)
