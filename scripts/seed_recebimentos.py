"""Seed one portal account per Recebimentos onboarding UI state.

Populates ``users`` / ``companies`` / ``accounts`` / ``company_billing_profiles``
and (for provisioned states) ``asaas_subaccount_links`` with column values
crafted to make ``GET /accounts/me`` and ``GET /accounts/subaccount/onboarding``
render each of the ten states from
``docs/design/portal-recebimentos-onboarding.md`` §4 in devbutter_frontend's
``/portal/recebimentos`` screen.

The frontend derives the visible state purely from the ``AsaasSubaccountLink``
row (via ``subaccount_status`` on ``/accounts/me``); it never needs a live
asaas_payment call for these fixtures. Only the four "unfinished" states
(AWAITING_ONBOARDING, PENDING_DOCUMENTS, UNDER_REVIEW, REJECTED) satisfy
``onboarding_required()`` and therefore actually trip the portal-wide gate in
``src/proxy.ts`` — the rest render the screen without locking the nav.

Usage (run it the same way you run the API, so it picks up the same
``DATABASE_URL``):

    # inside the api container
    docker compose exec api python -m scripts.seed_recebimentos

    # or locally, against infra's shared Postgres on host port 5436
    DATABASE_URL=postgresql+asyncpg://platform:platform@localhost:5436/platform \\
        poetry run python -m scripts.seed_recebimentos

Flags:
    --keep            don't delete existing seed rows first (default: reset them)
    --password PW      shared password for every seeded login (default: recebimentos123)
    --domain DOMAIN    email domain for the seeds (default: devbutter.test)
    --database-url URL override the connection string for this run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

# Allow `python scripts/seed_recebimentos.py` as well as `-m scripts.seed_recebimentos`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.accounts.models import (  # noqa: E402
    Account,
    AsaasSubaccountLink,
    Company,
    CompanyBillingProfile,
    User,
)
from app.core.security import hash_password  # noqa: E402
from app.core.validators.documents import is_valid_cnpj  # noqa: E402

NOW = datetime.now(UTC)


def _cnpj_with_check_digits(base12: str) -> str:
    """Append the two check digits to a 12-digit CNPJ base."""

    def digit(nums: str, weights: list[int]) -> str:
        total = sum(int(n) * w for n, w in zip(nums, weights, strict=True))
        rem = total % 11
        return "0" if rem < 2 else str(11 - rem)

    d1 = digit(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = digit(base12 + d1, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base12 + d1 + d2


@dataclass
class StateFixture:
    key: str
    company: str
    #: None -> no asaas_subaccount_links row at all (the "not_provisioned" case).
    link: dict[str, Any] | None
    note: str
    gated: bool = field(default=False)


def _link(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "asaas_account_id": None,
        "wallet_id": None,
        "provisioning_status": "NONE",
        "onboarding_status": None,
        "approval_status": None,
        "commercial_info_status": None,
        "bank_account_info_status": None,
        "documentation_status": None,
        "commercial_info_expires_at": None,
        "last_error": None,
        "closed_at": None,
    }
    base.update(overrides)
    return base


# One fixture per state. `gated` mirrors backend `onboarding_required()`:
# provisioning_status == "PROVISIONED" and approval_status != "APPROVED".
FIXTURES: list[StateFixture] = [
    StateFixture(
        key="not_provisioned",
        company="Ateliê Corvo (sem conta)",
        link=None,
        note="Sem subconta. A tela mostra 'será criada após o primeiro pagamento'.",
    ),
    StateFixture(
        key="AWAITING_ONBOARDING",
        company="Ateliê Corvo (provisionando)",
        link=_link(
            asaas_account_id="acc_await_0001",
            provisioning_status="PROVISIONED",
            onboarding_status="AWAITING_ONBOARDING",
            approval_status="PENDING",
        ),
        note="Spinner + polling. Portal bloqueado.",
        gated=True,
    ),
    StateFixture(
        key="PENDING_DOCUMENTS",
        company="Ateliê Corvo (documentos)",
        link=_link(
            asaas_account_id="acc_docs_0002",
            provisioning_status="PROVISIONED",
            onboarding_status="PENDING_DOCUMENTS",
            approval_status="PENDING",
            commercial_info_status="APPROVED",
            bank_account_info_status="APPROVED",
            documentation_status="PENDING",
        ),
        note=(
            "Lista de documentos pendentes (o payload real de pending_documents "
            "vem do asaas_payment; sem ele a tela usa o passo estático). Portal bloqueado."
        ),
        gated=True,
    ),
    StateFixture(
        key="UNDER_REVIEW",
        company="Ateliê Corvo (em análise)",
        link=_link(
            asaas_account_id="acc_review_0003",
            provisioning_status="PROVISIONED",
            onboarding_status="UNDER_REVIEW",
            approval_status="AWAITING_APPROVAL",
            commercial_info_status="APPROVED",
            bank_account_info_status="APPROVED",
            documentation_status="AWAITING_APPROVAL",
        ),
        note="Badge 'em análise'. Portal bloqueado.",
        gated=True,
    ),
    StateFixture(
        key="REJECTED",
        company="Ateliê Corvo (recusado)",
        link=_link(
            asaas_account_id="acc_reject_0004",
            provisioning_status="PROVISIONED",
            onboarding_status="REJECTED",
            approval_status="REJECTED",
            commercial_info_status="APPROVED",
            bank_account_info_status="APPROVED",
            documentation_status="REJECTED",
            last_error="Documento de identificação ilegível nas bordas",
        ),
        note="Motivo + reenviar. Portal bloqueado.",
        gated=True,
    ),
    StateFixture(
        key="APPROVED",
        company="Ateliê Corvo",
        link=_link(
            asaas_account_id="acc_approved_0005",
            wallet_id="wlt_9c2f4f2a8b1d",
            provisioning_status="PROVISIONED",
            onboarding_status="APPROVED",
            approval_status="APPROVED",
            commercial_info_status="APPROVED",
            bank_account_info_status="APPROVED",
            documentation_status="APPROVED",
        ),
        note="Badge verde, resumo, portal liberado.",
    ),
    StateFixture(
        key="COMMERCIAL_INFO_EXPIRING",
        company="Ateliê Corvo (dados a confirmar)",
        link=_link(
            asaas_account_id="acc_expiring_0006",
            wallet_id="wlt_expiring_06",
            provisioning_status="PROVISIONED",
            onboarding_status="APPROVED",
            approval_status="APPROVED",
            commercial_info_status="APPROVED",
            bank_account_info_status="APPROVED",
            documentation_status="APPROVED",
            commercial_info_expires_at=NOW + timedelta(days=15),
        ),
        note="Banner âmbar + formulário de commercial-info. Portal NÃO bloqueado.",
    ),
    StateFixture(
        key="COMMERCIAL_INFO_EXPIRED",
        company="Ateliê Corvo (dados expirados)",
        link=_link(
            asaas_account_id="acc_expired_0007",
            wallet_id="wlt_expired_07",
            provisioning_status="RESTRICTED",
            onboarding_status="APPROVED",
            approval_status="APPROVED",
            commercial_info_status="EXPIRED",
            bank_account_info_status="APPROVED",
            documentation_status="APPROVED",
            commercial_info_expires_at=NOW - timedelta(days=3),
        ),
        note="Banner vermelho + só o formulário de commercial-info.",
    ),
    StateFixture(
        key="CREATION_FAILED",
        company="Ateliê Corvo (falha na criação)",
        link=_link(
            provisioning_status="CREATION_FAILED",
            last_error="asaas 422 · cpfCnpj já em uso em outra conta",
        ),
        note="Mensagem de erro, sem ação do cliente.",
    ),
    StateFixture(
        key="CLOSED",
        company="Ateliê Corvo (encerrada)",
        link=_link(
            asaas_account_id="acc_closed_0009",
            wallet_id="wlt_closed_09",
            provisioning_status="CLOSED",
            onboarding_status="APPROVED",
            approval_status="APPROVED",
            closed_at=NOW - timedelta(days=1),
        ),
        note="'Conta encerrada, fale com o suporte'.",
    ),
]


def _email(key: str, domain: str) -> str:
    return f"recebimentos+{key.lower()}@{domain}"


def _billing_profile(company_id: uuid.UUID, legal_name: str, cnpj: str) -> CompanyBillingProfile:
    return CompanyBillingProfile(
        company_id=company_id,
        legal_name=legal_name,
        billing_email="financeiro@atelie-corvo.test",
        cnpj=cnpj,
        company_type="LIMITED",
        mobile_phone="11999990000",
        income_value=Decimal("42000.00"),
        address="Av. Paulista, 1000",
        address_number="1000",
        complement="Sala 402",
        province="Bela Vista",
        postal_code="01310100",
        site=None,
    )


async def _seed(
    session_factory: async_sessionmaker[Any],
    *,
    password: str,
    domain: str,
    reset: bool,
) -> list[tuple[StateFixture, str]]:
    emails = [_email(f.key, domain) for f in FIXTURES]
    password_hash = hash_password(password)
    seeded: list[tuple[StateFixture, str]] = []

    async with session_factory() as session:
        if reset:
            existing = (
                await session.execute(select(User.id).where(User.email.in_(emails)))
            ).scalars().all()
            if existing:
                # companies/accounts/links/profiles cascade off users.id (FK ondelete=CASCADE).
                await session.execute(delete(User).where(User.id.in_(existing)))
                await session.flush()

        for index, fixture in enumerate(FIXTURES):
            email = _email(fixture.key, domain)
            clash = (
                await session.execute(select(User.id).where(User.email == email))
            ).scalar_one_or_none()
            if clash is not None:
                print(f"  skip {fixture.key}: {email} already exists (use without --keep to reset)")
                continue

            cnpj = _cnpj_with_check_digits(f"1122334400{index:02d}")
            assert is_valid_cnpj(cnpj), cnpj

            user = User(
                id=uuid.uuid4(),
                email=email,
                password_hash=password_hash,
                name="Corvo Joalheria",
                phone="11999990000",
            )
            company = Company(id=uuid.uuid4(), user_id=user.id, name=fixture.company, cnpj=cnpj)
            account = Account(id=uuid.uuid4(), company_id=company.id, product_name="Portal")
            session.add_all([user, company, account])
            # CompanyBillingProfile / AsaasSubaccountLink have no ORM relationship
            # to Company, so the unit of work can't infer the insert ordering --
            # flush the parents first.
            await session.flush()

            session.add(_billing_profile(company.id, fixture.company, cnpj))
            if fixture.link is not None:
                session.add(AsaasSubaccountLink(company_id=company.id, **fixture.link))
            await session.flush()

            seeded.append((fixture, email))

        await session.commit()

    return seeded


def _print_summary(seeded: list[tuple[StateFixture, str]], password: str) -> None:
    if not seeded:
        print("\nNothing new seeded.")
        return
    key_w = max(len(f.key) for f, _ in seeded)
    email_w = max(len(e) for _, e in seeded)
    print(f"\nSeeded {len(seeded)} portal login(s) · senha: {password}\n")
    print(f"  {'STATE'.ljust(key_w)}  {'EMAIL'.ljust(email_w)}  GATE  NOTE")
    print(f"  {'-' * key_w}  {'-' * email_w}  ----  ----")
    for fixture, email in seeded:
        gate = "SIM " if fixture.gated else "não "
        print(f"  {fixture.key.ljust(key_w)}  {email.ljust(email_w)}  {gate}  {fixture.note}")
    print("\nGATE=SIM: onboarding_required é true — o portal inteiro redireciona")
    print("para /portal/recebimentos até a subconta ser aprovada.")


async def _main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--keep", action="store_true", help="don't reset existing seed rows first")
    parser.add_argument("--password", default="recebimentos123", help="shared login password")
    parser.add_argument("--domain", default="devbutter.test", help="email domain for the seeds")
    parser.add_argument("--database-url", default=None, help="override the connection string")
    args = parser.parse_args()

    if args.database_url:
        engine = create_async_engine(args.database_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
    else:
        from app.db.session import engine, session_factory  # noqa: PLC0415

    try:
        target = engine.url.render_as_string(hide_password=True)
        print(f"Seeding Recebimentos test accounts → {target}")
        seeded = await _seed(
            session_factory,
            password=args.password,
            domain=args.domain,
            reset=not args.keep,
        )
        _print_summary(seeded, args.password)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
