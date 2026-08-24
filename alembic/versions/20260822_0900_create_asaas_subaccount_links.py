"""create asaas_subaccount_links

Revision ID: 20260822_0900
Revises: 20260821_1400
Create Date: 2026-08-22 09:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_0900"
down_revision: Union[str, None] = "20260821_1400"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "asaas_subaccount_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("asaas_account_id", sa.String(length=64), nullable=False),
        sa.Column("wallet_id", sa.String(length=64), nullable=True),
        sa.Column("onboarding_status", sa.String(length=32), nullable=True),
        sa.Column("approval_status", sa.String(length=32), nullable=True),
        sa.Column("split_quota_percent", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("quota_set_by", sa.Uuid(), nullable=True),
        sa.Column("quota_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["quota_set_by"], ["accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_asaas_subaccount_links_company_id"),
        "asaas_subaccount_links",
        ["company_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_asaas_subaccount_links_company_id"), table_name="asaas_subaccount_links"
    )
    op.drop_table("asaas_subaccount_links")
