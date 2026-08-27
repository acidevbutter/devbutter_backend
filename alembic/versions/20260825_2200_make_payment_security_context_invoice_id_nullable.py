"""make payment_security_contexts.invoice_id nullable

Revision ID: 20260825_2200
Revises: 20260825_0900
Create Date: 2026-08-25 22:00:00.000000

Fixes N-01 from docs/prompts/payment-tokenization/runs/11-final-validation.md:
`app/payment_methods/models.py` declares `invoice_id: Mapped[uuid.UUID | None]`
(optional) -- correct per contract-07 section 1, since `invoice_id` is only
populated for `purpose=payment_method.create_and_pay` and must stay NULL for
`purpose=payment_method.create` (save a card without charging). But the
original `20260824_1200_create_payment_methods.py` created the column with
`nullable=False`, and `20260825_0900_extend_payment_security_contexts.py`
extended the table without ever touching `invoice_id`. The ORM's belief
(nullable) and the real Postgres constraint (NOT NULL) diverged silently,
because the test suite runs on SQLite (`Base.metadata.create_all()`), which
derives nullability straight from the Python type annotation and never sees
the real migration DDL.

Live impact confirmed against a real Postgres before this fix: `POST
/payment-methods/session` with `purpose=payment_method.create` (no
`invoiceId`, exactly as contract-07 section 1 requires for that purpose)
raised `asyncpg.exceptions.NotNullViolationError: null value in column
"invoice_id" ... violates not-null constraint`, surfaced to the client as a
raw uncaught `500 Internal Server Error`.

`amount_minor` and `currency` -- the sibling contract-07 section 2.2 fields
added alongside `invoice_id` for the `create_and_pay` case -- do NOT have
this problem: `20260825_0900_extend_payment_security_contexts.py` already
added both with `nullable=True`, matching the ORM (`int | None` / `str |
None`) and confirmed nullable in the live schema (`\\d
payment_security_contexts` before this migration). Only `invoice_id` needs
fixing here.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260825_2200"
down_revision: str | None = "20260825_0900"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "payment_security_contexts"
_COLUMN = "invoice_id"


def upgrade() -> None:
    op.alter_column(
        _TABLE, _COLUMN, existing_type=sa.Uuid(), nullable=True, existing_nullable=False
    )


def downgrade() -> None:
    # No pre-existing `payment_method.create` row (invoice_id IS NULL) can
    # legally be reverted to NOT NULL without data loss. If any such rows
    # exist when downgrading, this raises IntegrityError -- deliberately: a
    # silent data-losing downgrade would reintroduce exactly the class of
    # unnoticed model/schema divergence this migration exists to fix.
    op.alter_column(
        _TABLE, _COLUMN, existing_type=sa.Uuid(), nullable=False, existing_nullable=True
    )
