"""add conversations.ready_to_submit

Revision ID: 20260823_2200
Revises: 20260823_0900
Create Date: 2026-08-23 22:00:00.000000

Fluxo Cotação IA canvas: a resumed chat (devbutter_frontend's
db_conversation_id cookie) needs to know whether the last turn already
reached ready_to_submit without an extra round trip to butter_mind -- see
ConversationService.send_message/stream_message.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260823_2200"
down_revision: Union[str, None] = "20260823_0900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("ready_to_submit", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("conversations", "ready_to_submit", server_default=None)


def downgrade() -> None:
    op.drop_column("conversations", "ready_to_submit")
