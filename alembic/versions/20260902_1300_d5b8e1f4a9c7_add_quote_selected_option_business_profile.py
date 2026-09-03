"""add quote.selected_option_key + business_profile_snapshot (ADR-0004)

Revision ID: d5b8e1f4a9c7
Revises: a7f3c9d1e2b4
Create Date: 2026-09-02 13:00:00.000000

The Fluxo Cotação IA "turno 2" (ADR-0004) lets the visitor pick one of 2-3
proposed paths and answer a structured business profile. Both are carried onto
the Quote so the admin review queue shows the chosen path + the business
context without opening the conversation transcript.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5b8e1f4a9c7"
down_revision: Union[str, None] = "a7f3c9d1e2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "quotes",
        sa.Column("selected_option_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "quotes",
        sa.Column("business_profile_snapshot", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("quotes", "business_profile_snapshot")
    op.drop_column("quotes", "selected_option_key")
