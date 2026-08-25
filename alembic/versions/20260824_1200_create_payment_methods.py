"""create payment methods and replay-safe submission contexts

Revision ID: 20260824_1200
Revises: 20260823_2203
Create Date: 2026-08-24 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260824_1200"
down_revision: str | None = "20260823_2203"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_security_contexts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("context_id", sa.String(length=96), nullable=False, unique=True),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.Uuid(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("operation", sa.String(length=24), nullable=False),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="unused"),
        sa.Column("request_id", sa.String(length=36), nullable=True, unique=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_payment_security_contexts_expires_at", "payment_security_contexts", ["expires_at"])
    op.create_index("ix_payment_security_contexts_state", "payment_security_contexts", ["state"])
    op.create_table(
        "payment_methods",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("company_id", sa.Uuid(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asaas_customer_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="asaas"),
        sa.Column("provider_token_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("token_kid", sa.String(length=64), nullable=False),
        sa.Column("brand", sa.String(length=24), nullable=False),
        sa.Column("last4", sa.String(length=4), nullable=False),
        sa.Column("expiry_month", sa.Integer(), nullable=False),
        sa.Column("expiry_year", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_payment_methods_company_id", "payment_methods", ["company_id"])
    op.create_index("ix_payment_methods_asaas_customer_id", "payment_methods", ["asaas_customer_id"])
    op.create_index("ix_payment_methods_status", "payment_methods", ["status"])
    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("invoice_id", sa.Uuid(), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("payment_method_id", sa.Uuid(), sa.ForeignKey("payment_methods.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="processing"),
        sa.Column("provider_payment_id", sa.String(length=64), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("invoice_id", "idempotency_key", name="uq_payment_attempt_invoice_key"),
    )
    op.create_index("ix_payment_attempts_invoice_id", "payment_attempts", ["invoice_id"])
    op.create_index("ix_payment_attempts_state", "payment_attempts", ["state"])


def downgrade() -> None:
    op.drop_table("payment_attempts")
    op.drop_table("payment_methods")
    op.drop_table("payment_security_contexts")
