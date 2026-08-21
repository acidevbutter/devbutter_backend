from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.accounts.models import Account
from app.accounts.repository import AccountRepository
from app.accounts.schemas import (
    AccountRead,
    AccountSignup,
    AccountUpdate,
    LoginRequest,
    RefreshRequest,
    TokenPair,
)
from app.accounts.service import AccountService
from app.core.dependencies import DbSession
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import InvalidTokenError, TokenType, decode_token


def get_account_service(db: DbSession) -> AccountService:
    return AccountService(AccountRepository(db))


AccountServiceDep = Annotated[AccountService, Depends(get_account_service)]

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_account(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    service: AccountServiceDep,
) -> Account:
    if credentials is None:
        raise UnauthorizedError("Missing bearer token")
    try:
        account_id = decode_token(credentials.credentials, TokenType.ACCESS)
    except InvalidTokenError as exc:
        raise UnauthorizedError(str(exc)) from exc
    account = await service.get(account_id)
    if account is None or not account.is_active:
        raise UnauthorizedError("Account no longer active")
    return account


CurrentAccount = Annotated[Account, Depends(get_current_account)]


async def get_current_admin_account(account: CurrentAccount) -> Account:
    if not account.is_admin:
        raise ForbiddenError("Admin access required")
    return account


CurrentAdminAccount = Annotated[Account, Depends(get_current_admin_account)]

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post(
    "/signup",
    response_model=TokenPair,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
async def signup(payload: AccountSignup, service: AccountServiceDep) -> TokenPair:
    _, tokens = await service.signup(payload)
    return tokens


@router.post("/login", response_model=TokenPair, summary="Authenticate with email and password")
async def login(payload: LoginRequest, service: AccountServiceDep) -> TokenPair:
    _, tokens = await service.login(payload.email, payload.password)
    return tokens


@router.post(
    "/refresh", response_model=TokenPair, summary="Exchange a refresh token for a new token pair"
)
async def refresh(payload: RefreshRequest, service: AccountServiceDep) -> TokenPair:
    return await service.refresh(payload.refresh_token)


@router.get("/me", response_model=AccountRead, summary="Get the authenticated account")
async def get_me(account: CurrentAccount) -> AccountRead:
    return AccountRead.model_validate(account)


@router.patch(
    "/me", response_model=AccountRead, summary="Update the authenticated account's profile"
)
async def update_me(
    payload: AccountUpdate, account: CurrentAccount, service: AccountServiceDep
) -> AccountRead:
    updated = await service.update(account, payload)
    return AccountRead.model_validate(updated)
