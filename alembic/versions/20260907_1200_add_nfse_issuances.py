"""add NFS-e issuance outbox and requires_nfse on invoices

Revision ID: a1b2c3d4e5f6
Revises: f7a2d9c4e1b8
Create Date: 2026-09-07 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f7a2d9c4e1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("requires_nfse", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "nfse_issuances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("asaas_account_id", sa.String(length=64), nullable=False),
        sa.Column("asaas_payment_id", sa.String(length=64), nullable=False),
        sa.Column("asaas_nfse_id", sa.String(length=64), nullable=True),
        sa.Column("external_reference", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("nfse_number", sa.String(length=64), nullable=True),
        sa.Column("validation_code", sa.String(length=64), nullable=True),
        sa.Column("pdf_blob_id", sa.String(length=64), nullable=True),
        sa.Column("xml_blob_id", sa.String(length=64), nullable=True),
        sa.Column("document_url", sa.String(length=500), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_id", name="uq_nfse_issuances_invoice_id"),
        sa.UniqueConstraint("asaas_nfse_id", name="uq_nfse_issuances_asaas_nfse_id"),
        sa.UniqueConstraint("external_reference"),
    )
    op.create_index("ix_nfse_issuances_asaas_account_id", "nfse_issuances", ["asaas_account_id"])
    op.create_index("ix_nfse_issuances_asaas_payment_id", "nfse_issuances", ["asaas_payment_id"])
    op.create_index("ix_nfse_issuances_status", "nfse_issuances", ["status"])
    op.create_index("ix_nfse_issuances_next_attempt_at", "nfse_issuances", ["next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_nfse_issuances_next_attempt_at", table_name="nfse_issuances")
    op.drop_index("ix_nfse_issuances_status", table_name="nfse_issuances")
    op.drop_index("ix_nfse_issuances_asaas_payment_id", table_name="nfse_issuances")
    op.drop_index("ix_nfse_issuances_asaas_account_id", table_name="nfse_issuances")
    op.drop_table("nfse_issuances")
    op.drop_column("invoices", "requires_nfse")
