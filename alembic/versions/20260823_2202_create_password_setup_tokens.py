"""create password_setup_tokens

Revision ID: 20260823_2202
Revises: 20260823_2201
Create Date: 2026-08-23 22:02:00.000000

Fluxo Cotação IA canvas screen 1e: a visitor whose account was created
passwordless (auth_method="magic_link", see 20260823_0900) needs a real way
back in. Only token_hash is stored -- see
AccountRepository.create_password_setup_token.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260823_2202"
down_revision: Union[str, None] = "20260823_2201"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "password_setup_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_password_setup_tokens_user_id"), "password_setup_tokens", ["user_id"]
    )
    op.create_index(
        op.f("ix_password_setup_tokens_token_hash"),
        "password_setup_tokens",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_password_setup_tokens_token_hash"), table_name="password_setup_tokens")
    op.drop_index(op.f("ix_password_setup_tokens_user_id"), table_name="password_setup_tokens")
    op.drop_table("password_setup_tokens")
