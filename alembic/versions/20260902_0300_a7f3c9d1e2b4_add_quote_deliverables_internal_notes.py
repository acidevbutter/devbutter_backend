"""add quote deliverables + internal_notes

Revision ID: a7f3c9d1e2b4
Revises: c1d2e3f4a5b6
Create Date: 2026-09-02 03:00:00.000000

Backs the quote-detail screen's "entregáveis" list and "observações internas"
box (see "Admin Platform.dc.html" artboard 1d). deliverables is a JSON array
of free-form strings, edited/read as one blob like conversations.transcript;
internal_notes is Marcos-only text never surfaced to the client.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7f3c9d1e2b4"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "quotes",
        sa.Column(
            "deliverables",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "quotes",
        sa.Column("internal_notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("quotes", "internal_notes")
    op.drop_column("quotes", "deliverables")
