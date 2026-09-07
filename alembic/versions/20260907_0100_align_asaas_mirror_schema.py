"""align Layer A Asaas mirror metadata with ADR-0002

Revision ID: f7a2d9c4e1b8
Revises: d5b8e1f4a9c7
Create Date: 2026-09-07 01:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7a2d9c4e1b8"
down_revision: str | None = "d5b8e1f4a9c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "asaas_customer_links",
        sa.Column("external_reference", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "asaas_customer_links",
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_asaas_customer_links_external_reference"),
        "asaas_customer_links",
        ["external_reference"],
        unique=False,
    )
    op.add_column(
        "invoices", sa.Column("provider_payload_hash", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "invoices", sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("invoices", "last_event_at")
    op.drop_column("invoices", "provider_payload_hash")
    op.drop_index(
        op.f("ix_asaas_customer_links_external_reference"), table_name="asaas_customer_links"
    )
    op.drop_column("asaas_customer_links", "last_synced_at")
    op.drop_column("asaas_customer_links", "external_reference")
