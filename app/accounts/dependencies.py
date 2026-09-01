from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.accounts.models import Account, Company, User
from app.accounts.repository import AccountRepository
from app.accounts.service import AccountService
from app.core.clients.relay_client import RelayClient, get_relay_client
from app.core.dependencies import DbSession
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import InvalidTokenError, TokenType, decode_token
from app.notifications.service import NotificationService


def get_account_service(db: DbSession) -> AccountService:
    return AccountService(AccountRepository(db))


AccountServiceDep = Annotated[AccountService, Depends(get_account_service)]


def get_password_recovery_service(
    db: DbSession, relay_client: Annotated[RelayClient, Depends(get_relay_client)]
) -> AccountService:
    return AccountService(AccountRepository(db), NotificationService(relay_client))


PasswordRecoveryServiceDep = Annotated[AccountService, Depends(get_password_recovery_service)]

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
        claims = decode_token(credentials.credentials, TokenType.ACCESS)
    except InvalidTokenError as exc:
        raise UnauthorizedError(str(exc)) from exc
    user = await service.get_user(claims.user_id)
    account = await service.get_account_for_user(claims.account_id, claims.user_id)
    company = await service.get_company(account.company_id) if account else None
    if (
        user is None
        or not user.is_active
        or user.session_version != claims.session_version
        or account is None
        or company is None
    ):
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


async def get_optional_current_account(
    context: OptionalAuthenticatedContextDep,
) -> Account | None:
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
