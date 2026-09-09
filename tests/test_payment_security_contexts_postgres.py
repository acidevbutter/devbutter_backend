"""N-01 regression: payment_security_contexts.invoice_id nullability, tested
against a *real* Postgres via the real Alembic migration chain.

docs/prompts/payment-tokenization/runs/11-final-validation.md section N-01
(bloqueante): `app/payment_methods/models.py` declares
`invoice_id: Mapped[uuid.UUID | None]` (nullable), but the original
migration `20260824_1200_create_payment_methods.py` created the column
`nullable=False`, and `20260825_0900_extend_payment_security_contexts.py`
extended the table without ever touching `invoice_id` -- the divergence
survived. `purpose=payment_method.create` (save a card without charging --
no invoice involved, per docs/prompts/payment-tokenization/runs/
07-contract-freeze.md section 1) leaves `invoice_id` NULL, which Postgres
then rejected with `asyncpg.exceptions.NotNullViolationError`, surfaced to
the client as a raw uncaught 500.

Every other fixture in this suite (see tests/conftest.py) runs against
SQLite via `Base.metadata.create_all()`, which derives column nullability
straight from the Python type annotation -- it can *never* see this class of
bug, because it never runs a single line of Alembic DDL. This is the one
test file in the suite that talks to real Postgres and runs the real
migration chain, specifically to close that blind spot. It is not a general
Postgres test harness for the rest of the suite -- see 13-fix-invoice-id-
nullable.md for why a single, narrowly-scoped fixture was preferred over
adding a repo-wide Postgres test mode.

Requires a reachable Postgres superuser to create/drop a throwaway
database -- defaults to the exact `infra` shared-postgres this phase's
manual validation used (infra/docker-compose.yml's `shared-postgres`
service, published on host port 5436 per infra/.env's
POSTGRES_HOST_PORT, with infra/.env's POSTGRES_ADMIN_USER/PASSWORD).
Override with TEST_POSTGRES_ADMIN_URL if needed. Skips (does not fail) if
that Postgres isn't reachable, so a plain `poetry run pytest` in an
environment without `infra` running -- e.g. CI without docker-compose --
still passes clean; run `docker compose -f infra/docker-compose.yml up -d`
first to actually exercise this file.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from alembic.config import Config
from app.settings.config import settings

_ADMIN_URL = os.environ.get(
    "TEST_POSTGRES_ADMIN_URL",
    "postgresql://devbutter_admin:devbutter_admin@localhost:5436/postgres",
)
_BACKEND_ROOT = Path(__file__).resolve().parent.parent


async def _admin_reachable() -> bool:
    try:
        conn = await asyncpg.connect(_ADMIN_URL, timeout=2)
    except (OSError, asyncpg.PostgresError, TimeoutError):
        return False
    await conn.close()
    return True


@pytest.fixture
async def real_postgres_db():
    """Creates a throwaway Postgres database, runs the real Alembic
    migration chain (`alembic upgrade head`) against it -- not
    `Base.metadata.create_all()` -- and yields its SQLAlchemy async URL.
    Drops the database afterward regardless of test outcome.
    """
    if not await _admin_reachable():
        pytest.skip(
            f"No reachable Postgres at TEST_POSTGRES_ADMIN_URL ({_ADMIN_URL}). "
            "Start infra's shared-postgres to run this test: "
            "docker compose -f infra/docker-compose.yml up -d"
        )

    db_name = f"test_n01_{uuid.uuid4().hex[:16]}"
    admin_conn = await asyncpg.connect(_ADMIN_URL)
    try:
        await admin_conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await admin_conn.close()

    admin_sa_url = make_url(_ADMIN_URL)
    test_url = admin_sa_url.set(
        drivername="postgresql+asyncpg", database=db_name
    ).render_as_string(hide_password=False)

    # env.py (alembic/env.py) reads `settings.database_url` at run time to
    # build its own engine -- point the process-wide Settings singleton at
    # the throwaway database for the duration of `command.upgrade`.
    parsed = make_url(test_url)
    original = (
        settings.database_host,
        settings.database_port,
        settings.database_user,
        settings.database_password,
        settings.database_name,
    )
    settings.database_host = parsed.host or ""
    settings.database_port = parsed.port or 5432
    settings.database_user = parsed.username or ""
    settings.database_password = parsed.password or ""
    settings.database_name = parsed.database or ""
    try:
        alembic_cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
        # env.py itself drives its migration via `asyncio.run(...)`, which
        # cannot nest inside this test's own already-running event loop
        # (pytest-asyncio's asyncio_mode="auto") -- run it in a plain OS
        # thread, exactly like invoking `poetry run alembic upgrade head`
        # as a separate process would.
        await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
    finally:
        (
            settings.database_host,
            settings.database_port,
            settings.database_user,
            settings.database_password,
            settings.database_name,
        ) = original

    try:
        yield test_url
    finally:
        admin_conn = await asyncpg.connect(_ADMIN_URL)
        try:
            await admin_conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                db_name,
            )
            await admin_conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        finally:
            await admin_conn.close()


@pytest.mark.asyncio
async def test_invoice_id_is_nullable_after_real_migration(real_postgres_db: str) -> None:
    """After running the real Alembic chain against real Postgres,
    `payment_security_contexts.invoice_id` must be nullable, matching
    `app/payment_methods/models.py`'s `Mapped[uuid.UUID | None]`. Before the
    `20260825_2200` migration, this assertion failed: Postgres kept the
    original `nullable=False` from `20260824_1200` forever.
    """
    engine = create_async_engine(real_postgres_db)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'payment_security_contexts' "
                    "AND column_name = 'invoice_id'"
                )
            )
            row = result.one()
            assert row.is_nullable == "YES", (
                "payment_security_contexts.invoice_id is NOT NULL in the real "
                "Postgres schema (N-01 regression) -- purpose=payment_method.create "
                "session creation would raise asyncpg.exceptions.NotNullViolationError"
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_payment_method_create_context_insert_succeeds_with_null_invoice_id(
    real_postgres_db: str,
) -> None:
    """Row-level regression matching what
    `PaymentMethodService.create_context` (app/payment_methods/service.py)
    actually persists for `purpose=payment_method.create`: a
    `PaymentSecurityContext` with `invoice_id=None`. Against the
    pre-N-01-fix schema, this exact insert raised
    `asyncpg.exceptions.NotNullViolationError: null value in column
    "invoice_id" ... violates not-null constraint` -- reproduced live via
    HTTP before this migration existed (see 13-fix-invoice-id-nullable.md).
    """
    from app.accounts.models import Account, Company, User
    from app.payment_methods.models import PaymentSecurityContext

    engine = create_async_engine(real_postgres_db)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            user = User(
                email="n01-regression@example.com",
                name="N01 Regression",
                password_hash="not-a-real-hash",
            )
            company = Company(user=user, name="N01 Regression Co")
            account = Account(company=company, product_name="devbutter-platform")
            session.add_all([user, company, account])
            await session.flush()

            context = PaymentSecurityContext(
                context_id="secctx_n01_regression_test",
                account_id=account.id,
                company_id=company.id,
                purpose="payment_method.create",
                kid="n01-regression-kid",
                nonce=b"0" * 12,
                invoice_id=None,  # <- exactly what N-01 broke
                amount_minor=None,
                currency=None,
                expires_at=datetime.now(UTC) + timedelta(seconds=300),
            )
            session.add(context)
            await session.commit()  # raised IntegrityError before the fix

            await session.refresh(context)
            assert context.invoice_id is None
    finally:
        await engine.dispose()
