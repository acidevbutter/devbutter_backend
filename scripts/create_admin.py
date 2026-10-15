"""Create or promote a DevButter administrator.

The command is idempotent: it creates the local User, Company and Account when
the email is new; otherwise it promotes the existing user. A password is only
overwritten with ``--reset-password``.

Run inside the API container:

    docker compose exec api python -m scripts.create_admin \
      --email admin@example.com --name Administrador --password '...'

``ADMIN_EMAIL``, ``ADMIN_PASSWORD``, ``ADMIN_NAME`` and
``ADMIN_COMPANY_NAME`` are accepted as environment fallbacks.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account, Company, User
from app.core.security import hash_password
from app.db.session import session_factory


async def bootstrap_admin(
    session: AsyncSession,
    *,
    email: str,
    password: str | None,
    name: str | None,
    company_name: str | None,
    reset_password: bool,
) -> tuple[User, str]:
    """Create or promote one user and ensure it can authenticate as admin."""
    normalized_email = email.strip().lower()
    user = (
        await session.execute(select(User).where(User.email == normalized_email))
    ).scalar_one_or_none()

    if user is None:
        if not password:
            raise ValueError("new admin requires --password or ADMIN_PASSWORD")
        user = User(
            id=uuid.uuid4(),
            email=normalized_email,
            password_hash=hash_password(password),
            name=name or "Administrador",
            is_admin=True,
        )
        session.add(user)
        await session.flush()
        action = "created"
    else:
        user.is_admin = True
        user.is_active = True
        if name:
            user.name = name
        if password and (reset_password or not user.password_hash):
            user.password_hash = hash_password(password)
            user.auth_method = "password"
            action = "promoted + password reset"
        else:
            action = "promoted"

    company = (
        (
            await session.execute(
                select(Company).where(Company.user_id == user.id).order_by(Company.created_at.asc())
            )
        )
        .scalars()
        .first()
    )
    if company is None:
        company = Company(
            id=uuid.uuid4(),
            user_id=user.id,
            name=company_name or "DevButter Administração",
        )
        session.add(company)
        await session.flush()

    account = (
        (
            await session.execute(
                select(Account)
                .where(Account.company_id == company.id)
                .order_by(Account.created_at.asc())
            )
        )
        .scalars()
        .first()
    )
    if account is None:
        session.add(Account(id=uuid.uuid4(), company_id=company.id, product_name="Administração"))
        if action != "created":
            action = f"{action} + account created"

    return user, action


async def run(
    *,
    email: str,
    password: str | None,
    name: str | None,
    company_name: str | None,
    reset_password: bool,
) -> int:
    async with session_factory() as session:
        try:
            user, action = await bootstrap_admin(
                session,
                email=email,
                password=password,
                name=name,
                company_name=company_name,
                reset_password=reset_password,
            )
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        await session.commit()
        await session.refresh(user)

    print(f"OK: {user.email} -> {action} (id={user.id}, is_admin={user.is_admin})")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default=os.getenv("ADMIN_EMAIL"))
    parser.add_argument("--password", default=os.getenv("ADMIN_PASSWORD"))
    parser.add_argument("--name", default=os.getenv("ADMIN_NAME", "Administrador"))
    parser.add_argument(
        "--company-name",
        default=os.getenv("ADMIN_COMPANY_NAME", "DevButter Administração"),
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="overwrite the password of an existing user",
    )
    args = parser.parse_args()
    if not args.email:
        parser.error("informe --email ou ADMIN_EMAIL")
    raise SystemExit(
        asyncio.run(
            run(
                email=args.email,
                password=args.password,
                name=args.name,
                company_name=args.company_name,
                reset_password=args.reset_password,
            )
        )
    )


if __name__ == "__main__":
    main()
