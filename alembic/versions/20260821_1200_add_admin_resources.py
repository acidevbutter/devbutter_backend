"""add knowledge entries and webhook event audit records

Revision ID: 20260821_1200
Revises: fdbe523ccd11
Create Date: 2026-08-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260821_1200"
down_revision: Union[str, None] = "fdbe523ccd11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price_min", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("price_max", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("typical_duration", sa.String(length=100), nullable=False),
        sa.Column("ai_instructions", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("related_invoice_id", sa.Uuid(), nullable=True),
        sa.Column("processed_ok", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["related_invoice_id"], ["invoices.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_webhook_events_event_type"), "webhook_events", ["event_type"])
    op.create_index(op.f("ix_webhook_events_processed_ok"), "webhook_events", ["processed_ok"])
    op.create_index(op.f("ix_webhook_events_received_at"), "webhook_events", ["received_at"])
    op.create_index(
        op.f("ix_webhook_events_related_invoice_id"),
        "webhook_events",
        ["related_invoice_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_webhook_events_related_invoice_id"), table_name="webhook_events")
    op.drop_index(op.f("ix_webhook_events_received_at"), table_name="webhook_events")
    op.drop_index(op.f("ix_webhook_events_processed_ok"), table_name="webhook_events")
    op.drop_index(op.f("ix_webhook_events_event_type"), table_name="webhook_events")
    op.drop_table("webhook_events")
    op.drop_table("knowledge_entries")
