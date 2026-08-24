import uuid
from dataclasses import dataclass
from typing import Any

from app.accounts.models import Account, Company, User
from app.accounts.repository import AccountRepository
from app.accounts.schemas import (
    AccountCreate,
    AccountSignup,
    AccountUpdate,
    CompanyCreate,
    TokenPair,
)
from app.core.decorators import log_errors
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    UnauthorizedError,
    ValidationFailedError,
)
from app.core.security import (
    InvalidTokenError,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


@dataclass
class AccountLinkResult:
    """Result of AccountService.get_or_create_from_extraction. `tokens` is
    None when the contact email matched an existing User -- the caller must
    not treat that User as logged in (see
    docs/plano-onboarding-conversa-primeiro.md §3.1: auto-login on an email
    typed by an anonymous visitor would let anyone claim someone else's
    account just by knowing their address). `created` tells the caller
    whether a brand-new User/Company/Account was made.
    """

    user: User
    company: Company
    account: Account
    tokens: TokenPair | None
    created: bool


@log_errors
class AccountService:
    def __init__(self, repository: AccountRepository):
        self.repository = repository

    async def signup(self, payload: AccountSignup) -> tuple[User, Company, Account, TokenPair]:
        user, company, account = await self.repository.create(
            payload, hash_password(payload.password)
        )
        return user, company, account, self._issue_tokens(user.id, account.id)

    async def login(self, email: str, password: str) -> tuple[User, Account, TokenPair]:
        user = await self.repository.get_user_by_email(email)
        if user is None or not user.is_active or user.password_hash is None:
            raise UnauthorizedError("Invalid email or password")
        if not verify_password(password, user.password_hash):
            raise UnauthorizedError("Invalid email or password")
        accounts = await self.repository.list_for_user(user.id)
        if not accounts:
            raise UnauthorizedError("User has no active account")
        return user, accounts[0], self._issue_tokens(user.id, accounts[0].id)

    async def get_or_create_from_extraction(
        self, extraction: dict[str, Any], password: str | None = None
    ) -> AccountLinkResult:
        """Resolves the User/Company/Account that should own a Quote drafted
        by a not-yet-authenticated visitor, from the contact fields
        butter_mind's DiagnosisExtraction returns (see
        docs/plano-onboarding-conversa-primeiro.md §2). If contact_email
        matches an existing User, that account is reused but no TokenPair is
        issued (see AccountLinkResult) -- the caller is expected to notify
        that User out of band instead of granting the visitor a session.
        `password`, when given, is only used for a brand-new account (see
        Fluxo Cotação IA canvas screen 1e) -- it's silently ignored when the
        email matches an existing account, since a visitor typing someone
        else's email must never be able to set that account's password.
        """
        contact_email = extraction.get("contact_email")
        if not contact_email:
            raise ValidationFailedError("The conversation extraction has no contact_email")

        existing_user = await self.repository.get_user_by_email(contact_email)
        if existing_user is not None:
            accounts = await self.repository.list_for_user(existing_user.id)
            if not accounts:
                raise ConflictError(f"User {existing_user.id} has no account to link a quote to")
            company = await self.repository.get_company(accounts[0].company_id)
            if company is None:
                raise ConflictError(f"Company {accounts[0].company_id} not found")
            return AccountLinkResult(
                user=existing_user,
                company=company,
                account=accounts[0],
                tokens=None,
                created=False,
            )

        password_hash = hash_password(password) if password else None
        user, company, account = await self.repository.create_from_extraction(
            extraction, password_hash=password_hash
        )
        return AccountLinkResult(
            user=user,
            company=company,
            account=account,
            tokens=self._issue_tokens(user.id, account.id),
            created=True,
        )

    async def issue_password_setup_token(self, user_id: uuid.UUID) -> str:
        raw_token, _ = await self.repository.create_password_setup_token(user_id)
        return raw_token

    async def check_password_setup_token(self, raw_token: str) -> User:
        token = await self.repository.get_valid_password_setup_token(raw_token)
        if token is None:
            raise NotFoundError("This link is invalid, expired, or already used")
        user = await self.repository.get_user(token.user_id)
        if user is None:
            raise NotFoundError("This link is invalid, expired, or already used")
        return user

    async def complete_password_setup(self, raw_token: str, password: str) -> TokenPair:
        token = await self.repository.get_valid_password_setup_token(raw_token)
        if token is None:
            raise NotFoundError("This link is invalid, expired, or already used")
        user = await self.repository.get_user(token.user_id)
        if user is None:
            raise NotFoundError("This link is invalid, expired, or already used")
        accounts = await self.repository.list_for_user(user.id)
        if not accounts:
            raise ConflictError(f"User {user.id} has no account")
        await self.repository.set_password(user, hash_password(password))
        await self.repository.mark_password_setup_token_used(token)
        return self._issue_tokens(user.id, accounts[0].id)

    async def refresh(self, refresh_token: str) -> TokenPair:
        try:
            user_id, account_id = decode_token(refresh_token, TokenType.REFRESH)
        except InvalidTokenError as exc:
            raise UnauthorizedError(str(exc)) from exc
        user = await self.repository.get_user(user_id)
        account = await self.repository.get_for_user(account_id, user_id)
        if user is None or not user.is_active or account is None:
            raise UnauthorizedError("User or selected account is no longer active")
        return self._issue_tokens(user.id, account.id)

    async def get(self, account_id: uuid.UUID) -> Account | None:
        return await self.repository.get(account_id)

    async def get_user(self, user_id: uuid.UUID) -> User | None:
        return await self.repository.get_user(user_id)

    async def get_account_for_user(
        self, account_id: uuid.UUID, user_id: uuid.UUID
    ) -> Account | None:
        return await self.repository.get_for_user(account_id, user_id)

    async def list_accounts(self, user_id: uuid.UUID) -> list[Account]:
        return await self.repository.list_for_user(user_id)

    async def get_company(self, company_id: uuid.UUID) -> Company | None:
        return await self.repository.get_company(company_id)

    async def list_companies(self, user_id: uuid.UUID) -> list[Company]:
        return await self.repository.list_companies_for_user(user_id)

    async def create_account(
        self, user: User, current_company: Company, payload: AccountCreate
    ) -> tuple[Account, Company]:
        company = await self.repository.get_company(payload.company_id or current_company.id)
        if company is None or company.user_id != user.id:
            raise NotFoundError("Company not found")
        return await self.repository.create_account(company.id, payload), company

    async def create_company(self, user_id: uuid.UUID, payload: CompanyCreate) -> Company:
        return await self.repository.create_company(user_id, payload)

    async def select_account(self, user: User, account_id: uuid.UUID) -> TokenPair:
        account = await self.repository.get_for_user(account_id, user.id)
        if account is None:
            raise UnauthorizedError("Account is not available to this user")
        return self._issue_tokens(user.id, account.id)

    async def update(
        self, user: User, company: Company, account: Account, payload: AccountUpdate
    ) -> Account:
        return await self.repository.update(user, company, account, payload)

    def _issue_tokens(self, user_id: uuid.UUID, account_id: uuid.UUID) -> TokenPair:
        return TokenPair(
            access_token=create_access_token(user_id, account_id),
            refresh_token=create_refresh_token(user_id, account_id),
        )
