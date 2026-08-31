"""add selolibertaria relay + rotation fields

Revision ID: 20260828_1000
Revises: 80849bed4242
Create Date: 2026-08-28 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260828_1000"
down_revision: Union[str, None] = '80849bed4242'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # asaas_subaccount_links: bookkeeping for the DevButter -> Selolibertaria
    # credential relay + rotation (see docs/prompts/asaas-payment-integration/
    # 03-prompt-devbutter-relay-e-rotacao.md, entregaveis 2 and 3). Never adds
    # a column that stores the api_key itself -- only references/status.
    op.add_column(
        "asaas_subaccount_links",
        sa.Column("access_token_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "asaas_subaccount_links",
        sa.Column(
            "key_version", sa.Integer(), nullable=False, server_default="1"
        ),
    )
    op.add_column(
        "asaas_subaccount_links",
        sa.Column(
            "selolibertaria_relay_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "asaas_subaccount_links",
        sa.Column(
            "selolibertaria_relay_attempted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "asaas_subaccount_links",
        sa.Column("selolibertaria_relay_last_error", sa.Text(), nullable=True),
    )

    # webhook_events: optional event_id column so any webhook-shaped delivery
    # that carries its own idempotency key (Asaas events with `id`, and the
    # Selolibertaria -> DevButter reactive rotate-key request, which is not
    # itself an Asaas webhook but reuses this table per CONTRATO-HMAC.md's
    # "idempotencia de negocio... event_id em asaas_relay_event / WebhookEvent")
    # can be deduped without a second table.
    op.add_column(
        "webhook_events",
        sa.Column("event_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f("ix_webhook_events_event_id"),
        "webhook_events",
        ["event_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_webhook_events_event_id"), table_name="webhook_events")
    op.drop_column("webhook_events", "event_id")
    op.drop_column("asaas_subaccount_links", "selolibertaria_relay_last_error")
    op.drop_column("asaas_subaccount_links", "selolibertaria_relay_attempted_at")
    op.drop_column("asaas_subaccount_links", "selolibertaria_relay_status")
    op.drop_column("asaas_subaccount_links", "key_version")
    op.drop_column("asaas_subaccount_links", "access_token_id")
