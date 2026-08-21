import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account, AsaasCustomerLink
from app.accounts.schemas import AccountSignup, AccountUpdate
from app.core.exceptions import ConflictError


class AccountRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, account_id: uuid.UUID) -> Account | None:
        return await self.session.get(Account, account_id)

    async def get_by_email(self, email: str) -> Account | None:
        result = await self.session.execute(select(Account).where(Account.email == email))
        return result.scalar_one_or_none()

    async def create(self, payload: AccountSignup, password_hash: str) -> Account:
        account = Account(
            email=payload.email,
            password_hash=password_hash,
            name=payload.name,
            document=payload.document,
            phone=payload.phone,
            business_name=payload.business_name,
        )
        self.session.add(account)
        await self._commit_or_raise_conflict(payload.email)
        await self.session.refresh(account)
        return account

    async def update(self, account: Account, payload: AccountUpdate) -> Account:
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(account, field, value)
        await self.session.commit()
        await self.session.refresh(account)
        return account

    async def get_asaas_customer_link(self, account_id: uuid.UUID) -> AsaasCustomerLink | None:
        result = await self.session.execute(
            select(AsaasCustomerLink).where(AsaasCustomerLink.account_id == account_id)
        )
        return result.scalar_one_or_none()

    async def create_asaas_customer_link(
        self, account_id: uuid.UUID, asaas_customer_id: str
    ) -> AsaasCustomerLink:
        link = AsaasCustomerLink(account_id=account_id, asaas_customer_id=asaas_customer_id)
        self.session.add(link)
        await self.session.commit()
        await self.session.refresh(link)
        return link

    async def _commit_or_raise_conflict(self, email: str) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(f"An account with email {email!r} already exists") from exc
