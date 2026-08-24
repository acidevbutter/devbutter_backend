"""add users.auth_method, make users.password_hash nullable

Revision ID: 20260823_0900
Revises: 20260822_0902
Create Date: 2026-08-23 09:00:00.000000

docs/plano-onboarding-conversa-primeiro.md: a User can now be created by
AccountService.get_or_create_from_extraction from an AI conversation's
contact fields, before the visitor ever sets a password. auth_method is
added as password|magic_link (default "password" so every existing row
stays on the password flow it already uses) and password_hash becomes
nullable to allow that passwordless creation path.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260823_0900"
down_revision: Union[str, None] = "20260822_0902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("users", "password_hash", existing_type=sa.String(length=200), nullable=True)
    op.add_column(
        "users",
        sa.Column(
            "auth_method", sa.String(length=20), nullable=False, server_default="password"
        ),
    )
    op.alter_column("users", "auth_method", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "auth_method")
    op.alter_column("users", "password_hash", existing_type=sa.String(length=200), nullable=False)
