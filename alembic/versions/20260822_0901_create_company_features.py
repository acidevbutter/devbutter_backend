"""create company_features

Revision ID: 20260822_0901
Revises: 20260822_0900
Create Date: 2026-08-22 09:01:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_0901"
down_revision: Union[str, None] = "20260822_0900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_features",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("feature_code", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("asaas_access_token_id", sa.String(length=64), nullable=True),
        sa.Column("enabled_by", sa.Uuid(), nullable=True),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["enabled_by"], ["accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_company_features_company_id"), "company_features", ["company_id"]
    )
    op.create_index(
        "ix_company_features_company_feature",
        "company_features",
        ["company_id", "feature_code"],
    )


def downgrade() -> None:
    op.drop_index("ix_company_features_company_feature", table_name="company_features")
    op.drop_index(op.f("ix_company_features_company_id"), table_name="company_features")
    op.drop_table("company_features")
