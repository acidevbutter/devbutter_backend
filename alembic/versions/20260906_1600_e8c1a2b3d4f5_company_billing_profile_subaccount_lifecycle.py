"""company_billing_profiles + asaas_subaccount_links lifecycle columns

Revision ID: e8c1a2b3d4f5
Revises: d5b8e1f4a9c7
Create Date: 2026-09-06 16:00:00.000000

Adds the "dados de recebimento" collected at signup (company_billing_profiles)
and the columns asaas_subaccount_links needs to drive the subaccount lifecycle:
a provisioning status/error (so a failed create is retryable from the admin),
the per-dimension cadastral statuses mirrored from ACCOUNT_STATUS_* webhooks,
the annual commercialInfo expiry, and a closed_at. asaas_account_id becomes
nullable so a CREATION_FAILED row can exist without one.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8c1a2b3d4f5"
down_revision: Union[str, None] = "d5b8e1f4a9c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_billing_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=False),
        sa.Column("billing_email", sa.String(length=320), nullable=False),
        sa.Column("cnpj", sa.String(length=14), nullable=False),
        sa.Column("company_type", sa.String(length=20), nullable=False),
        sa.Column("mobile_phone", sa.String(length=20), nullable=False),
        sa.Column("income_value", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("address", sa.String(length=255), nullable=False),
        sa.Column("address_number", sa.String(length=32), nullable=False),
        sa.Column("complement", sa.String(length=255), nullable=True),
        sa.Column("province", sa.String(length=120), nullable=False),
        sa.Column("postal_code", sa.String(length=8), nullable=False),
        sa.Column("site", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_company_billing_profiles_company_id",
        "company_billing_profiles",
        ["company_id"],
        unique=True,
    )

    op.add_column(
        "asaas_subaccount_links",
        sa.Column(
            "provisioning_status",
            sa.String(length=32),
            nullable=False,
            server_default="PROVISIONED",
        ),
    )
    # Existing rows were all successful links; drop the server_default afterwards
    # so new rows must set it explicitly.
    op.alter_column("asaas_subaccount_links", "provisioning_status", server_default=None)
    op.add_column("asaas_subaccount_links", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column(
        "asaas_subaccount_links",
        sa.Column("commercial_info_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "asaas_subaccount_links",
        sa.Column("bank_account_info_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "asaas_subaccount_links",
        sa.Column("documentation_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "asaas_subaccount_links",
        sa.Column("commercial_info_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "asaas_subaccount_links",
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column(
        "asaas_subaccount_links",
        "asaas_account_id",
        existing_type=sa.String(length=64),
        nullable=True,
    )
    op.create_index(
        "ix_asaas_subaccount_links_asaas_account_id",
        "asaas_subaccount_links",
        ["asaas_account_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_asaas_subaccount_links_asaas_account_id", table_name="asaas_subaccount_links"
    )
    op.alter_column(
        "asaas_subaccount_links",
        "asaas_account_id",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.drop_column("asaas_subaccount_links", "closed_at")
    op.drop_column("asaas_subaccount_links", "commercial_info_expires_at")
    op.drop_column("asaas_subaccount_links", "documentation_status")
    op.drop_column("asaas_subaccount_links", "bank_account_info_status")
    op.drop_column("asaas_subaccount_links", "commercial_info_status")
    op.drop_column("asaas_subaccount_links", "last_error")
    op.drop_column("asaas_subaccount_links", "provisioning_status")

    op.drop_index(
        "ix_company_billing_profiles_company_id", table_name="company_billing_profiles"
    )
    op.drop_table("company_billing_profiles")
