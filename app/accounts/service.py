import uuid

from app.accounts.models import Account
from app.accounts.repository import AccountRepository
from app.accounts.schemas import AccountSignup, AccountUpdate, TokenPair
from app.core.decorators import log_errors
from app.core.exceptions import UnauthorizedError
from app.core.security import (
    InvalidTokenError,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


@log_errors
class AccountService:
    def __init__(self, repository: AccountRepository):
        self.repository = repository

    async def signup(self, payload: AccountSignup) -> tuple[Account, TokenPair]:
        account = await self.repository.create(payload, hash_password(payload.password))
        return account, self._issue_tokens(account.id)

    async def login(self, email: str, password: str) -> tuple[Account, TokenPair]:
        account = await self.repository.get_by_email(email)
        if account is None or not account.is_active:
            raise UnauthorizedError("Invalid email or password")
        if not verify_password(password, account.password_hash):
            raise UnauthorizedError("Invalid email or password")
        return account, self._issue_tokens(account.id)

    async def refresh(self, refresh_token: str) -> TokenPair:
        try:
            account_id = decode_token(refresh_token, TokenType.REFRESH)
        except InvalidTokenError as exc:
            raise UnauthorizedError(str(exc)) from exc
        account = await self.repository.get(account_id)
        if account is None or not account.is_active:
            raise UnauthorizedError("Account no longer active")
        return self._issue_tokens(account.id)

    async def get(self, account_id: uuid.UUID) -> Account | None:
        return await self.repository.get(account_id)

    async def update(self, account: Account, payload: AccountUpdate) -> Account:
        return await self.repository.update(account, payload)

    def _issue_tokens(self, account_id: uuid.UUID) -> TokenPair:
        return TokenPair(
            access_token=create_access_token(account_id),
            refresh_token=create_refresh_token(account_id),
        )
