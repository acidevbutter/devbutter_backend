"""drop selolibertaria relay + rotation fields from asaas_subaccount_links

The DevButter -> Selolibertaria credential relay is gone: the store reaches
Asaas through the asaas_payment mTLS proxy, which owns the credential.
`asaas_subaccount_links` keeps only the read-only reference
(company_id <-> asaas_account_id <-> wallet_id) plus the split-quota fields.

`webhook_events.event_id` (added in the same revision that added these
columns) is left in place -- it is a generic emitter-assigned idempotency
key, not relay-specific.

Revision ID: c1d2e3f4a5b6
Revises: 20260828_1000
Create Date: 2026-09-01 01:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "20260828_1000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("asaas_subaccount_links", "selolibertaria_relay_last_error")
    op.drop_column("asaas_subaccount_links", "selolibertaria_relay_attempted_at")
    op.drop_column("asaas_subaccount_links", "selolibertaria_relay_status")
    op.drop_column("asaas_subaccount_links", "key_version")
    op.drop_column("asaas_subaccount_links", "access_token_id")


def downgrade() -> None:
    op.add_column(
        "asaas_subaccount_links",
        sa.Column("access_token_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "asaas_subaccount_links",
        sa.Column("key_version", sa.Integer(), nullable=False, server_default="1"),
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
