"""merge NFS-e issuance and subaccount onboarding migration heads

Revision ID: 2e682c38d771
Revises: e8c1a2b3d4f5, a1b2c3d4e5f6
Create Date: 2026-09-09 00:48:49.989187

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2e682c38d771'
down_revision: Union[str, None] = ('e8c1a2b3d4f5', 'a1b2c3d4e5f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
