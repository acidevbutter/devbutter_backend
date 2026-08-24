"""add companies between users and accounts

Revision ID: 20260821_1400
Revises: 20260821_1330
Create Date: 2026-08-21 14:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260821_1400"
down_revision: Union[str, None] = "20260821_1330"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "document",
        new_column_name="cpf",
        existing_type=sa.String(length=20),
        existing_nullable=True,
    )
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("cnpj", sa.String(length=20), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companies_user_id"), "companies", ["user_id"])
    op.add_column("accounts", sa.Column("company_id", sa.Uuid(), nullable=True))

    op.execute(
        """
        INSERT INTO companies (id, user_id, name, cnpj, created_at, updated_at)
        SELECT
            accounts.id,
            accounts.user_id,
            COALESCE(accounts.business_name, accounts.product_name, 'Empresa principal'),
            CASE
                WHEN length(regexp_replace(COALESCE(users.cpf, ''), '\\D', '', 'g')) = 14
                    THEN users.cpf
                ELSE NULL
            END,
            accounts.created_at,
            accounts.updated_at
        FROM accounts
        JOIN users ON users.id = accounts.user_id
        """
    )
    op.execute("UPDATE accounts SET company_id = id")
    op.execute(
        """
        UPDATE users
        SET cpf = NULL
        WHERE length(regexp_replace(COALESCE(cpf, ''), '\\D', '', 'g')) = 14
        """
    )

    op.alter_column("accounts", "company_id", existing_type=sa.Uuid(), nullable=False)
    op.create_foreign_key(
        "fk_accounts_company_id_companies",
        "accounts",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("accounts", "business_name")
    op.drop_column("accounts", "user_id")

    op.add_column("asaas_customer_links", sa.Column("company_id", sa.Uuid(), nullable=True))
    op.execute(
        """
        UPDATE asaas_customer_links
        SET company_id = accounts.company_id
        FROM accounts
        WHERE accounts.id = asaas_customer_links.account_id
        """
    )
    op.alter_column("asaas_customer_links", "company_id", existing_type=sa.Uuid(), nullable=False)
    op.create_foreign_key(
        "fk_asaas_customer_links_company_id_companies",
        "asaas_customer_links",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_index(op.f("ix_asaas_customer_links_account_id"), table_name="asaas_customer_links")
    op.drop_column("asaas_customer_links", "account_id")
    op.create_index(
        op.f("ix_asaas_customer_links_company_id"),
        "asaas_customer_links",
        ["company_id"],
        unique=True,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Cannot safely downgrade Company after accounts/products have been reassigned between companies."
    )
