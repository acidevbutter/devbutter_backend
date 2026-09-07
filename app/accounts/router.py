import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, File, UploadFile, status

from app.accounts.billing_service import onboarding_required
from app.accounts.dependencies import (
    AccountBillingServiceDep,
    AccountServiceDep,
    CurrentAccount,
    CurrentCompany,
    CurrentUser,
    PasswordRecoveryServiceDep,
)
from app.accounts.models import Account, AsaasSubaccountLink, Company, User
from app.accounts.schemas import (
    AccountCreate,
    AccountRead,
    AccountSelect,
    AccountSelectionRead,
    AccountSignup,
    AccountUpdate,
    BillingProfileInput,
    BillingProfileRead,
    CompanyCreate,
    CompanyRead,
    LoginRequest,
    PasswordRecoveryCheck,
    PasswordRecoveryComplete,
    PasswordRecoveryRequest,
    PasswordRecoveryRequestAccepted,
    PasswordSetupCheck,
    PasswordSetupComplete,
    RefreshRequest,
    SubaccountStatusRead,
    TokenPair,
)
from app.core.exceptions import ValidationFailedError

_MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
_ALLOWED_DOCUMENT_MIME = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
}

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


@router.post(
    "/password-recovery",
    response_model=PasswordRecoveryRequestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request a portal password-recovery link",
)
async def request_password_recovery(
    payload: PasswordRecoveryRequest, service: PasswordRecoveryServiceDep
) -> PasswordRecoveryRequestAccepted:
    await service.request_password_recovery(str(payload.email))
    return PasswordRecoveryRequestAccepted()


@router.get(
    "/password-recovery/{token}",
    response_model=PasswordRecoveryCheck,
    summary="Check a password-recovery link before rendering the form",
)
async def check_password_recovery(token: str, service: AccountServiceDep) -> PasswordRecoveryCheck:
    return await service.check_password_recovery(token)


@router.post(
    "/password-recovery/complete",
    response_model=TokenPair,
    status_code=status.HTTP_200_OK,
    summary="Replace a password from a recovery link and log in",
)
async def complete_password_recovery(
    payload: PasswordRecoveryComplete, service: AccountServiceDep
) -> TokenPair:
    return await service.complete_password_recovery(payload.token, payload.password)


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
    link: AsaasSubaccountLink | None = None,
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
        onboarding_required=onboarding_required(link),
        subaccount_status=(
            SubaccountStatusRead.model_validate(link) if link is not None else None
        ),
    )


@router.get(
    "/me", response_model=AccountRead, summary="Get the authenticated user and selected account"
)
async def get_me(
    account: CurrentAccount,
    company: CurrentCompany,
    user: CurrentUser,
    service: AccountServiceDep,
    billing: AccountBillingServiceDep,
) -> AccountRead:
    accounts = await service.list_accounts(user.id)
    companies = {item.id: item for item in await service.list_companies(user.id)}
    link = await billing.get_link(company.id)
    return _account_read(user, company, account, accounts, companies, link)


@router.patch(
    "/me", response_model=AccountRead, summary="Update the authenticated account's profile"
)
async def update_me(
    payload: AccountUpdate,
    account: CurrentAccount,
    company: CurrentCompany,
    user: CurrentUser,
    service: AccountServiceDep,
    billing: AccountBillingServiceDep,
) -> AccountRead:
    updated = await service.update(user, company, account, payload)
    refreshed_user = await service.get_user(user.id)
    assert refreshed_user is not None
    accounts = await service.list_accounts(user.id)
    companies = {item.id: item for item in await service.list_companies(user.id)}
    link = await billing.get_link(company.id)
    return _account_read(refreshed_user, company, updated, accounts, companies, link)


@router.get(
    "/billing-profile",
    response_model=BillingProfileRead | None,
    summary="Get the company's dados de recebimento (Asaas subaccount inputs)",
)
async def get_billing_profile(
    company: CurrentCompany, billing: AccountBillingServiceDep
) -> BillingProfileRead | None:
    profile = await billing.get_profile(company.id)
    return BillingProfileRead.model_validate(profile) if profile is not None else None


@router.put(
    "/billing-profile",
    response_model=BillingProfileRead,
    summary="Create or replace the company's dados de recebimento",
)
async def put_billing_profile(
    payload: BillingProfileInput,
    company: CurrentCompany,
    billing: AccountBillingServiceDep,
) -> BillingProfileRead:
    profile = await billing.upsert_profile(company.id, payload)
    return BillingProfileRead.model_validate(profile)


@router.get(
    "/subaccount/onboarding",
    summary="Onboarding status + pending documents for the company's subaccount",
)
async def get_subaccount_onboarding(
    company: CurrentCompany, billing: AccountBillingServiceDep
) -> dict[str, Any]:
    return await billing.get_onboarding(company.id)


@router.post(
    "/subaccount/resend-activation",
    summary="Resend the subaccount's Asaas activation link",
)
async def resend_subaccount_activation(
    company: CurrentCompany, billing: AccountBillingServiceDep
) -> dict[str, Any]:
    return await billing.resend_activation(company.id)


@router.get(
    "/subaccount/commercial-info",
    summary="Read the subaccount's commercialInfo (annual reconfirmation)",
)
async def get_subaccount_commercial_info(
    company: CurrentCompany, billing: AccountBillingServiceDep
) -> dict[str, Any]:
    return await billing.get_commercial_info(company.id)


@router.post(
    "/subaccount/commercial-info",
    summary="Confirm/update the subaccount's commercialInfo",
)
async def update_subaccount_commercial_info(
    company: CurrentCompany,
    billing: AccountBillingServiceDep,
    payload: Annotated[dict[str, Any], Body(...)],
) -> dict[str, Any]:
    return await billing.update_commercial_info(company.id, payload)


@router.post(
    "/subaccount/documents/{group_id}",
    summary="Upload one file to an Asaas document group (no onboardingUrl)",
)
async def upload_subaccount_document(
    group_id: str,
    company: CurrentCompany,
    billing: AccountBillingServiceDep,
    file: Annotated[UploadFile, File(...)],
) -> dict[str, Any]:
    content = await file.read()
    if len(content) > _MAX_DOCUMENT_BYTES:
        raise ValidationFailedError("File exceeds the 10 MB limit")
    content_type = file.content_type or "application/octet-stream"
    if content_type not in _ALLOWED_DOCUMENT_MIME:
        raise ValidationFailedError(f"Unsupported file type {content_type!r}")
    return await billing.upload_document(
        company.id,
        group_id,
        filename=file.filename or "document",
        content=content,
        content_type=content_type,
    )


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
