import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.accounts.models import Account, Company, User
from app.accounts.repository import AccountRepository
from app.accounts.schemas import (
    AccountCreate,
    AccountRead,
    AccountSelect,
    AccountSelectionRead,
    AccountSignup,
    AccountUpdate,
    CompanyCreate,
    CompanyRead,
    LoginRequest,
    PasswordSetupCheck,
    PasswordSetupComplete,
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


class AuthenticatedContext:
    def __init__(self, user: User, company: Company, account: Account):
        self.user = user
        self.company = company
        self.account = account


async def get_authenticated_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    service: AccountServiceDep,
) -> AuthenticatedContext:
    if credentials is None:
        raise UnauthorizedError("Missing bearer token")
    try:
        user_id, account_id = decode_token(credentials.credentials, TokenType.ACCESS)
    except InvalidTokenError as exc:
        raise UnauthorizedError(str(exc)) from exc
    user = await service.get_user(user_id)
    account = await service.get_account_for_user(account_id, user_id)
    company = await service.get_company(account.company_id) if account else None
    if user is None or not user.is_active or account is None or company is None:
        raise UnauthorizedError("User or selected account is no longer active")
    return AuthenticatedContext(user, company, account)


AuthenticatedContextDep = Annotated[AuthenticatedContext, Depends(get_authenticated_context)]


async def get_optional_authenticated_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    service: AccountServiceDep,
) -> AuthenticatedContext | None:
    """Same resolution as get_authenticated_context, but a missing/invalid
    token means "visitor", not a 401 -- used by conversations/router.py so a
    chat can start and run without an account (see
    docs/plano-onboarding-conversa-primeiro.md).
    """
    if credentials is None:
        return None
    try:
        return await get_authenticated_context(credentials, service)
    except UnauthorizedError:
        return None


OptionalAuthenticatedContextDep = Annotated[
    AuthenticatedContext | None, Depends(get_optional_authenticated_context)
]


async def get_current_account(context: AuthenticatedContextDep) -> Account:
    return context.account


async def get_optional_current_account(context: OptionalAuthenticatedContextDep) -> Account | None:
    return context.account if context else None


async def get_current_user(context: AuthenticatedContextDep) -> User:
    return context.user


async def get_current_company(context: AuthenticatedContextDep) -> Company:
    return context.company


CurrentAccount = Annotated[Account, Depends(get_current_account)]
CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentCompany = Annotated[Company, Depends(get_current_company)]
OptionalCurrentAccount = Annotated[Account | None, Depends(get_optional_current_account)]


async def get_current_admin_account(account: CurrentAccount, user: CurrentUser) -> Account:
    if not user.is_admin:
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
    _, _, _, tokens = await service.signup(payload)
    return tokens


@router.post("/login", response_model=TokenPair, summary="Authenticate with email and password")
async def login(payload: LoginRequest, service: AccountServiceDep) -> TokenPair:
    _, _, tokens = await service.login(payload.email, payload.password)
    return tokens


@router.post(
    "/refresh", response_model=TokenPair, summary="Exchange a refresh token for a new token pair"
)
async def refresh(payload: RefreshRequest, service: AccountServiceDep) -> TokenPair:
    return await service.refresh(payload.refresh_token)


@router.get(
    "/password-setup/{token}",
    response_model=PasswordSetupCheck,
    summary="Check a password-setup link before rendering the form",
)
async def check_password_setup(token: str, service: AccountServiceDep) -> PasswordSetupCheck:
    user = await service.check_password_setup_token(token)
    return PasswordSetupCheck(email=user.email)


@router.post(
    "/password-setup",
    response_model=TokenPair,
    summary="Set a password from a password-setup link and log in",
)
async def complete_password_setup(
    payload: PasswordSetupComplete, service: AccountServiceDep
) -> TokenPair:
    return await service.complete_password_setup(payload.token, payload.password)


def _account_selection(account: Account, company: Company) -> AccountSelectionRead:
    return AccountSelectionRead(
        id=account.id,
        product_name=account.product_name,
        company_id=company.id,
        company_name=company.name,
        cnpj=company.cnpj,
        created_at=account.created_at,
    )


def _account_read(
    user: User,
    company: Company,
    account: Account,
    accounts: list[Account],
    companies: dict[uuid.UUID, Company],
) -> AccountRead:
    return AccountRead(
        id=account.id,
        user_id=user.id,
        email=user.email,
        name=user.name,
        cpf=user.cpf,
        phone=user.phone,
        product_name=account.product_name,
        company_id=company.id,
        company_name=company.name,
        cnpj=company.cnpj,
        is_admin=user.is_admin,
        is_active=user.is_active,
        created_at=account.created_at,
        accounts=[_account_selection(item, companies[item.company_id]) for item in accounts],
    )


@router.get(
    "/me", response_model=AccountRead, summary="Get the authenticated user and selected account"
)
async def get_me(
    account: CurrentAccount,
    company: CurrentCompany,
    user: CurrentUser,
    service: AccountServiceDep,
) -> AccountRead:
    accounts = await service.list_accounts(user.id)
    companies = {item.id: item for item in await service.list_companies(user.id)}
    return _account_read(user, company, account, accounts, companies)


@router.patch(
    "/me", response_model=AccountRead, summary="Update the authenticated account's profile"
)
async def update_me(
    payload: AccountUpdate,
    account: CurrentAccount,
    company: CurrentCompany,
    user: CurrentUser,
    service: AccountServiceDep,
) -> AccountRead:
    updated = await service.update(user, company, account, payload)
    refreshed_user = await service.get_user(user.id)
    assert refreshed_user is not None
    accounts = await service.list_accounts(user.id)
    companies = {item.id: item for item in await service.list_companies(user.id)}
    return _account_read(refreshed_user, company, updated, accounts, companies)


@router.get(
    "", response_model=list[AccountSelectionRead], summary="List the user's accounts/products"
)
async def list_accounts(
    user: CurrentUser, service: AccountServiceDep
) -> list[AccountSelectionRead]:
    accounts = await service.list_accounts(user.id)
    companies = {company.id: company for company in await service.list_companies(user.id)}
    return [_account_selection(account, companies[account.company_id]) for account in accounts]


@router.post("", response_model=AccountSelectionRead, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: AccountCreate,
    company: CurrentCompany,
    user: CurrentUser,
    service: AccountServiceDep,
) -> AccountSelectionRead:
    account, owner_company = await service.create_account(user, company, payload)
    return _account_selection(account, owner_company)


@router.get("/companies", response_model=list[CompanyRead])
async def list_companies(user: CurrentUser, service: AccountServiceDep) -> list[CompanyRead]:
    companies = await service.list_companies(user.id)
    return [CompanyRead.model_validate(company) for company in companies]


@router.post("/companies", response_model=CompanyRead, status_code=status.HTTP_201_CREATED)
async def create_company(
    payload: CompanyCreate, user: CurrentUser, service: AccountServiceDep
) -> CompanyRead:
    return CompanyRead.model_validate(await service.create_company(user.id, payload))


@router.post("/select", response_model=TokenPair, summary="Select the active account/product")
async def select_account(
    payload: AccountSelect, user: CurrentUser, service: AccountServiceDep
) -> TokenPair:
    return await service.select_account(user, payload.account_id)
