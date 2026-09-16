from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account, Company
from app.core.security import verify_password
from scripts.create_admin import bootstrap_admin


async def test_bootstrap_admin_creates_login_hierarchy(db_session: AsyncSession) -> None:
    user, action = await bootstrap_admin(
        db_session,
        email="ADMIN@devbutter.test ",
        password="safe-password",
        name="Admin",
        company_name="DevButter",
        reset_password=False,
    )
    await db_session.commit()

    assert action == "created"
    assert user.email == "admin@devbutter.test"
    assert user.is_admin is True
    assert verify_password("safe-password", user.password_hash or "")
    company = (
        await db_session.execute(select(Company).where(Company.user_id == user.id))
    ).scalar_one()
    account = (
        await db_session.execute(select(Account).where(Account.company_id == company.id))
    ).scalar_one()
    assert account.product_name == "Administração"


async def test_bootstrap_admin_promotes_without_resetting_password(
    db_session: AsyncSession,
) -> None:
    user, _ = await bootstrap_admin(
        db_session,
        email="admin@devbutter.test",
        password="first-password",
        name="Admin",
        company_name=None,
        reset_password=False,
    )
    await db_session.commit()
    original_hash = user.password_hash
    user.is_admin = False
    await db_session.commit()

    promoted, action = await bootstrap_admin(
        db_session,
        email="admin@devbutter.test",
        password="other-password",
        name=None,
        company_name=None,
        reset_password=False,
    )
    await db_session.commit()

    assert action == "promoted"
    assert promoted.is_admin is True
    assert promoted.password_hash == original_hash
    assert verify_password("first-password", promoted.password_hash or "")
