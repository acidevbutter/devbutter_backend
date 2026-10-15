"""company relay project routing

Revision ID: f1a2b3c4d5e6
Revises: 2e682c38d771
Create Date: 2026-10-15 07:20:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "2e682c38d771"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("relay_project_id", sa.String(length=64), nullable=True))
    op.add_column("companies", sa.Column("relay_queue", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "relay_queue")
    op.drop_column("companies", "relay_project_id")
