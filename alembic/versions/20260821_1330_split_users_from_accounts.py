"""split user identity from account/product context

Revision ID: 20260821_1330
Revises: 20260821_1200
Create Date: 2026-08-21 13:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260821_1330"
down_revision: Union[str, None] = "20260821_1200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("document", sa.String(length=20), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.add_column("accounts", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.add_column("accounts", sa.Column("product_name", sa.String(length=200), nullable=True))

    op.execute(
        """
        INSERT INTO users (
            id, email, password_hash, name, document, phone, is_admin, is_active, created_at, updated_at
        )
        SELECT id, email, password_hash, name, document, phone, is_admin, is_active, created_at, updated_at
        FROM accounts
        """
    )
    op.execute(
        """
        UPDATE accounts
        SET user_id = id,
            product_name = COALESCE(business_name, 'Conta principal')
        """
    )

    op.alter_column("accounts", "user_id", existing_type=sa.Uuid(), nullable=False)
    op.alter_column("accounts", "product_name", existing_type=sa.String(length=200), nullable=False)
    op.create_foreign_key(
        "fk_accounts_user_id_users", "accounts", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )

    op.drop_column("accounts", "is_active")
    op.drop_column("accounts", "is_admin")
    op.drop_column("accounts", "phone")
    op.drop_column("accounts", "document")
    op.drop_column("accounts", "name")
    op.drop_column("accounts", "password_hash")
    op.drop_column("accounts", "email")


def downgrade() -> None:
    raise RuntimeError(
        "Cannot safely downgrade user/account separation after users own multiple accounts."
    )
