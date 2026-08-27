import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import (
    Account,
    AsaasCustomerLink,
    AsaasSubaccountLink,
    Company,
    CompanyFeature,
    PasswordRecoveryToken,
    PasswordSetupToken,
    User,
)
from app.accounts.schemas import AccountCreate, AccountSignup, AccountUpdate, CompanyCreate
from app.core.exceptions import ConflictError

_PASSWORD_SETUP_TOKEN_TTL = timedelta(days=7)
_PASSWORD_RECOVERY_TOKEN_TTL = timedelta(minutes=45)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


class AccountRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, account_id: uuid.UUID) -> Account | None:
        return await self.session.get(Account, account_id)

    async def get_for_user(self, account_id: uuid.UUID, user_id: uuid.UUID) -> Account | None:
        result = await self.session.execute(
            select(Account)
            .join(Company, Company.id == Account.company_id)
            .where(Account.id == account_id, Company.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> list[Account]:
        result = await self.session.execute(
            select(Account)
            .join(Company, Company.id == Account.company_id)
            .where(Company.user_id == user_id)
            .order_by(Account.created_at.asc())
        )
        return list(result.scalars())

    async def get_user(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_user_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_user_by_normalized_email(self, email: str) -> User | None:
        result = await self.session.execute(
            select(User).where(func.lower(User.email) == email.strip().lower())
        )
        return result.scalar_one_or_none()

    async def get_company(self, company_id: uuid.UUID) -> Company | None:
        return await self.session.get(Company, company_id)

    async def list_companies_for_user(self, user_id: uuid.UUID) -> list[Company]:
        result = await self.session.execute(
            select(Company).where(Company.user_id == user_id).order_by(Company.created_at.asc())
        )
        return list(result.scalars())

    async def create(
        self, payload: AccountSignup, password_hash: str
    ) -> tuple[User, Company, Account]:
        user = User(
            id=uuid.uuid4(),
            email=payload.email,
            password_hash=password_hash,
            name=payload.name,
            cpf=payload.cpf,
            phone=payload.phone,
        )
        company = Company(
            id=uuid.uuid4(),
            user_id=user.id,
            name=payload.company_name or payload.product_name or "Empresa principal",
            cnpj=payload.cnpj,
        )
        account = Account(
            company_id=company.id,
            product_name=payload.product_name or "Conta principal",
        )
        self.session.add(user)
        self.session.add(company)
        self.session.add(account)
        await self._commit_or_raise_conflict(payload.email)
        await self.session.refresh(user)
        await self.session.refresh(company)
        await self.session.refresh(account)
        return user, company, account

    async def create_from_extraction(
        self, extraction: dict[str, Any], password_hash: str | None = None
    ) -> tuple[User, Company, Account]:
        """Creates a User/Company/Account from butter_mind's DiagnosisExtraction
        contact fields (contact_name/contact_email/contact_phone/company_name/
        cnpj) -- the visitor-onboarding counterpart of `create()`, used when a
        conversation completes without a prior signup (see
        AccountService.get_or_create_from_extraction). `password_hash` is set
        when the visitor typed a password directly in the Fluxo Cotação IA
        contact step (canvas screen 1e); otherwise the User is created
        passwordless (password_hash=None, auth_method="magic_link") and the
        caller is expected to issue a PasswordSetupToken instead.
        """
        email = extraction["contact_email"]
        name = extraction.get("contact_name") or email
        user = User(
            id=uuid.uuid4(),
            email=email,
            password_hash=password_hash,
            auth_method="password" if password_hash else "magic_link",
            name=name,
            phone=extraction.get("contact_phone"),
        )
        company = Company(
            id=uuid.uuid4(),
            user_id=user.id,
            name=extraction.get("company_name") or name,
            cnpj=extraction.get("cnpj"),
        )
        account = Account(company_id=company.id, product_name="Diagnóstico IA")
        self.session.add(user)
        self.session.add(company)
        self.session.add(account)
        await self._commit_or_raise_conflict(email)
        await self.session.refresh(user)
        await self.session.refresh(company)
        await self.session.refresh(account)
        return user, company, account

    async def create_account(self, company_id: uuid.UUID, payload: AccountCreate) -> Account:
        account = Account(
            company_id=company_id,
            product_name=payload.product_name,
        )
        self.session.add(account)
        await self.session.commit()
        await self.session.refresh(account)
        return account

    async def create_company(self, user_id: uuid.UUID, payload: CompanyCreate) -> Company:
        company = Company(user_id=user_id, **payload.model_dump())
        self.session.add(company)
        await self.session.commit()
        await self.session.refresh(company)
        return company

    async def update(
        self, user: User, company: Company, account: Account, payload: AccountUpdate
    ) -> Account:
        values = payload.model_dump(exclude_unset=True)
        for field in ("name", "cpf", "phone"):
            if field in values:
                setattr(user, field, values[field])
        for field in ("product_name",):
            if field in values:
                setattr(account, field, values[field])
        if "company_name" in values:
            company.name = values["company_name"]
        if "cnpj" in values:
            company.cnpj = values["cnpj"]
        await self.session.commit()
        await self.session.refresh(account)
        return account

    async def get_asaas_customer_link(self, company_id: uuid.UUID) -> AsaasCustomerLink | None:
        result = await self.session.execute(
            select(AsaasCustomerLink).where(AsaasCustomerLink.company_id == company_id)
        )
        return result.scalar_one_or_none()

    async def create_asaas_customer_link(
        self, company_id: uuid.UUID, asaas_customer_id: str
    ) -> AsaasCustomerLink:
        link = AsaasCustomerLink(company_id=company_id, asaas_customer_id=asaas_customer_id)
        self.session.add(link)
        await self.session.commit()
        await self.session.refresh(link)
        return link

    async def create_password_setup_token(
        self, user_id: uuid.UUID
    ) -> tuple[str, PasswordSetupToken]:
        raw_token = secrets.token_urlsafe(32)
        token = PasswordSetupToken(
            user_id=user_id,
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(UTC) + _PASSWORD_SETUP_TOKEN_TTL,
        )
        self.session.add(token)
        await self.session.commit()
        await self.session.refresh(token)
        return raw_token, token

    async def get_valid_password_setup_token(self, raw_token: str) -> PasswordSetupToken | None:
        result = await self.session.execute(
            select(PasswordSetupToken).where(
                PasswordSetupToken.token_hash == _hash_token(raw_token)
            )
        )
        token = result.scalar_one_or_none()
        if token is None or token.used_at is not None:
            return None
        expires_at = token.expires_at
        # SQLite (tests) returns naive datetimes for DateTime(timezone=True)
        # columns even though the value was written as UTC -- Postgres
        # doesn't have this issue, but normalize here so both behave alike.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at < datetime.now(UTC):
            return None
        return token

    async def mark_password_setup_token_used(self, token: PasswordSetupToken) -> None:
        token.used_at = datetime.now(UTC)
        await self.session.commit()

    async def create_password_recovery_token(
        self, user_id: uuid.UUID
    ) -> tuple[str, PasswordRecoveryToken]:
        now = datetime.now(UTC)
        await self.session.execute(
            update(PasswordRecoveryToken)
            .where(
                PasswordRecoveryToken.user_id == user_id,
                PasswordRecoveryToken.used_at.is_(None),
                PasswordRecoveryToken.expires_at > now,
            )
            .values(used_at=now)
        )
        raw_token = secrets.token_urlsafe(32)
        token = PasswordRecoveryToken(
            user_id=user_id,
            token_hash=_hash_token(raw_token),
            expires_at=now + _PASSWORD_RECOVERY_TOKEN_TTL,
        )
        self.session.add(token)
        try:
            await self.session.commit()
        except SQLAlchemyError:
            await self.session.rollback()
            raise
        await self.session.refresh(token)
        return raw_token, token

    async def set_password(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        user.auth_method = "password"
        await self.session.commit()
        await self.session.refresh(user)

    async def _commit_or_raise_conflict(self, email: str) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(f"An account with email {email!r} already exists") from exc


class AsaasSubaccountLinkRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_company(self, company_id: uuid.UUID) -> AsaasSubaccountLink | None:
        result = await self.session.execute(
            select(AsaasSubaccountLink).where(AsaasSubaccountLink.company_id == company_id)
        )
        return result.scalar_one_or_none()

    async def get_by_asaas_account_id(self, asaas_account_id: str) -> AsaasSubaccountLink | None:
        result = await self.session.execute(
            select(AsaasSubaccountLink).where(
                AsaasSubaccountLink.asaas_account_id == asaas_account_id
            )
        )
        return result.scalar_one_or_none()

    async def create(self, record: AsaasSubaccountLink) -> AsaasSubaccountLink:
        self.session.add(record)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("This company already has a payment method configured") from exc
        await self.session.refresh(record)
        return record

    async def update(self, record: AsaasSubaccountLink) -> AsaasSubaccountLink:
        await self.session.commit()
        await self.session.refresh(record)
        return record


class CompanyFeatureRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_active(self, company_id: uuid.UUID, feature_code: str) -> CompanyFeature | None:
        result = await self.session.execute(
            select(CompanyFeature).where(
                CompanyFeature.company_id == company_id,
                CompanyFeature.feature_code == feature_code,
                CompanyFeature.status == "active",
            )
        )
        return result.scalar_one_or_none()

    async def list_for_company(self, company_id: uuid.UUID) -> list[CompanyFeature]:
        result = await self.session.execute(
            select(CompanyFeature)
            .where(CompanyFeature.company_id == company_id)
            .order_by(CompanyFeature.updated_at.desc())
        )
        return list(result.scalars())

    async def create(self, record: CompanyFeature) -> CompanyFeature:
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def update(self, record: CompanyFeature) -> CompanyFeature:
        await self.session.commit()
        await self.session.refresh(record)
        return record
