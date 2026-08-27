"""extend payment_security_contexts with contract-07 ownership/replay fields

Revision ID: 20260825_0900
Revises: 20260824_1200
Create Date: 2026-08-25 09:00:00.000000

Aligns `payment_security_contexts` with the frozen ledger schema in
docs/prompts/payment-tokenization/runs/07-contract-freeze.md section 4:

- `operation` -> `purpose` (contract-07 corrects the earlier `operation`
  enum to the `purpose` values fixed by the crypto contract's AAD).
- `request_id` -> `request_id_used`, only ever written at consumption time.
- new columns: `kid`, `allowed_method`, `allowed_path`, `amount_minor`,
  `currency`, `failure_reason_code`, `processing_started_at` -- required to
  add the `processing` state to the `unused -> processing -> consumed`
  transition and to bind a context to the exact kid/route it was issued for.
- `UNIQUE(kid, nonce)`: a nonce must never repeat for a given kid, even
  across different contexts (contract-07 section 4, rule 4).
- `UNIQUE(account_id, request_id_used)`: replaces the old single-column
  unique on `request_id` -- ANSI NULL semantics mean multiple `unused`/
  `processing` rows with a NULL `request_id_used` do not collide, so this
  only rejects a genuine requestId reuse by the same subject (rule 5).

No production traffic depends on the previous shape (this table has never
shipped past NO-GO review), so this migration rewrites columns directly
rather than carrying a deprecated `operation`/`request_id` alongside the
new names.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_0900"
down_revision: str | None = "20260824_1200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "payment_security_contexts"


def upgrade() -> None:
    op.alter_column(_TABLE, "operation", new_column_name="purpose", type_=sa.String(length=80))
    op.alter_column(_TABLE, "request_id", new_column_name="request_id_used")
    op.drop_constraint("payment_security_contexts_request_id_key", _TABLE, type_="unique")

    op.add_column(_TABLE, sa.Column("kid", sa.String(length=64), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("allowed_method", sa.String(length=8), nullable=False, server_default="POST"),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "allowed_path",
            sa.String(length=120),
            nullable=False,
            server_default="/payment-methods",
        ),
    )
    op.add_column(_TABLE, sa.Column("amount_minor", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("currency", sa.String(length=3), nullable=True))
    op.add_column(_TABLE, sa.Column("failure_reason_code", sa.String(length=64), nullable=True))
    op.add_column(
        _TABLE, sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True)
    )

    # No pre-existing rows can have a real kid (the flow was never wired to
    # main.py before this phase); backfill defensively so the NOT NULL below
    # cannot fail against leftover local/dev data, then lock the column down.
    op.execute(sa.text(f"UPDATE {_TABLE} SET kid = 'unknown' WHERE kid IS NULL"))
    op.alter_column(_TABLE, "kid", nullable=False)

    op.create_unique_constraint(
        "uq_payment_security_contexts_kid_nonce", _TABLE, ["kid", "nonce"]
    )
    op.create_unique_constraint(
        "uq_payment_security_contexts_account_request",
        _TABLE,
        ["account_id", "request_id_used"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_payment_security_contexts_account_request", _TABLE, type_="unique"
    )
    op.drop_constraint("uq_payment_security_contexts_kid_nonce", _TABLE, type_="unique")

    op.drop_column(_TABLE, "processing_started_at")
    op.drop_column(_TABLE, "failure_reason_code")
    op.drop_column(_TABLE, "currency")
    op.drop_column(_TABLE, "amount_minor")
    op.drop_column(_TABLE, "allowed_path")
    op.drop_column(_TABLE, "allowed_method")
    op.drop_column(_TABLE, "kid")

    op.create_unique_constraint(
        "payment_security_contexts_request_id_key", _TABLE, ["request_id_used"]
    )
    op.alter_column(_TABLE, "request_id_used", new_column_name="request_id")
    op.alter_column(_TABLE, "purpose", new_column_name="operation", type_=sa.String(length=24))
