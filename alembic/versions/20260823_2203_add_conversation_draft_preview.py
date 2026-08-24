"""add conversations.draft_preview

Revision ID: 20260823_2203
Revises: 20260823_2202
Create Date: 2026-08-23 22:03:00.000000

Stores the safe scope preview produced by the latest diagnosis turn so the
portal can resume at the review gate without calling the LLM again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260823_2203"
down_revision: str | None = "20260823_2202"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("draft_preview", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "draft_preview")
