"""add quote_reference_counters, quotes.reference

Revision ID: 20260823_2201
Revises: 20260823_2200
Create Date: 2026-08-23 22:01:00.000000

Fluxo Cotação IA canvas (screens 1f/1g) addresses a quote by a human-readable
"COT-1000" id, not the raw UUID. quote_reference_counters is a plain table
instead of a Postgres SEQUENCE so it behaves identically under the in-memory
SQLite test suite (schema built from the SQLAlchemy models via
Base.metadata.create_all, not by running this migration) and real Postgres
-- see QuoteRepository._next_reference.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260823_2201"
down_revision: Union[str, None] = "20260823_2200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "quote_reference_counters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("next_value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column("quotes", sa.Column("reference", sa.String(length=16), nullable=True))

    conn = op.get_bind()
    quote_ids = [row[0] for row in conn.execute(sa.text("SELECT id FROM quotes ORDER BY created_at ASC"))]
    next_value = 1000
    for quote_id in quote_ids:
        conn.execute(
            sa.text("UPDATE quotes SET reference = :reference WHERE id = :id"),
            {"reference": f"COT-{next_value}", "id": quote_id},
        )
        next_value += 1
    conn.execute(
        sa.text("INSERT INTO quote_reference_counters (id, next_value) VALUES (1, :next_value)"),
        {"next_value": next_value},
    )

    op.alter_column("quotes", "reference", nullable=False)
    op.create_unique_constraint("uq_quotes_reference", "quotes", ["reference"])
    op.create_index(op.f("ix_quotes_reference"), "quotes", ["reference"])


def downgrade() -> None:
    op.drop_index(op.f("ix_quotes_reference"), table_name="quotes")
    op.drop_constraint("uq_quotes_reference", "quotes", type_="unique")
    op.drop_column("quotes", "reference")
    op.drop_table("quote_reference_counters")
