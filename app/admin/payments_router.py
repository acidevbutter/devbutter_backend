from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.accounts.repository import AsaasSubaccountLinkRepository
from app.accounts.router import CurrentAdminAccount
from app.admin.payments_service import AdminPaymentsService
from app.core.clients.asaas_client import AsaasClient, get_asaas_client
from app.core.dependencies import DbSession

router = APIRouter(prefix="/admin", tags=["admin-payments"])


def get_admin_payments_service(
    asaas_client: Annotated[AsaasClient, Depends(get_asaas_client)],
    db: DbSession,
) -> AdminPaymentsService:
    return AdminPaymentsService(asaas_client, AsaasSubaccountLinkRepository(db))


AdminPaymentsServiceDep = Annotated[AdminPaymentsService, Depends(get_admin_payments_service)]


# -- Subaccounts --------------------------------------------------


@router.get("/subaccounts", summary="List Asaas subaccounts")
async def list_subaccounts(
    _admin: CurrentAdminAccount,
    service: AdminPaymentsServiceDep,
    name: str | None = None,
    email: str | None = None,
    cpfCnpj: str | None = None,
    walletId: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict[str, Any]:
    filters = {
        "name": name,
        "email": email,
        "cpfCnpj": cpfCnpj,
        "walletId": walletId,
        "limit": limit,
        "offset": offset,
    }
    return await service.list_subaccounts({k: v for k, v in filters.items() if v is not None})


@router.get("/subaccounts/{account_id}", summary="Retrieve an Asaas subaccount")
async def get_subaccount(
    account_id: str, _admin: CurrentAdminAccount, service: AdminPaymentsServiceDep
) -> dict[str, Any]:
    return await service.get_subaccount(account_id)


@router.post("/subaccounts", summary="Create an Asaas subaccount")
async def create_subaccount(
    payload: dict[str, Any], _admin: CurrentAdminAccount, service: AdminPaymentsServiceDep
) -> dict[str, Any]:
    return await service.create_subaccount(payload)


@router.delete("/subaccounts/{account_id}", summary="Close an Asaas subaccount")
async def close_subaccount(
    account_id: str, _admin: CurrentAdminAccount, service: AdminPaymentsServiceDep
) -> dict[str, Any]:
    return await service.close_subaccount(account_id)


@router.post(
    "/subaccounts/{account_id}/resend-activation-link",
    summary="Resend a subaccount's activation link",
)
async def resend_activation_link(
    account_id: str, _admin: CurrentAdminAccount, service: AdminPaymentsServiceDep
) -> dict[str, Any]:
    return await service.resend_activation_link(account_id)


# -- Access tokens --------------------------------------------------


@router.get("/subaccounts/{account_id}/access-tokens", summary="List a subaccount's API keys")
async def list_access_tokens(
    account_id: str, _admin: CurrentAdminAccount, service: AdminPaymentsServiceDep
) -> dict[str, Any]:
    return await service.list_access_tokens(account_id)


@router.post(
    "/subaccounts/{account_id}/access-tokens", summary="Create an additional API key"
)
async def create_access_token(
    account_id: str,
    payload: dict[str, Any],
    _admin: CurrentAdminAccount,
    service: AdminPaymentsServiceDep,
) -> dict[str, Any]:
    return await service.create_access_token(account_id, payload)


@router.delete(
    "/subaccounts/{account_id}/access-tokens/{token_id}", summary="Revoke an API key"
)
async def revoke_access_token(
    account_id: str,
    token_id: str,
    _admin: CurrentAdminAccount,
    service: AdminPaymentsServiceDep,
) -> dict[str, Any]:
    return await service.revoke_access_token(account_id, token_id)


# -- Split rules --------------------------------------------------


@router.get("/subaccounts/{account_id}/split-rules", summary="List a subaccount's split rules")
async def list_split_rules(
    account_id: str, _admin: CurrentAdminAccount, service: AdminPaymentsServiceDep
) -> list[dict[str, Any]]:
    return await service.list_split_rules(account_id)


@router.post("/subaccounts/{account_id}/split-rules", summary="Create a split rule")
async def create_split_rule(
    account_id: str,
    payload: dict[str, Any],
    _admin: CurrentAdminAccount,
    service: AdminPaymentsServiceDep,
) -> dict[str, Any]:
    return await service.create_split_rule(account_id, payload)


@router.patch(
    "/subaccounts/{account_id}/split-rules/{rule_id}",
    summary="Activate, deactivate, or adjust a split rule's value",
)
async def update_split_rule(
    account_id: str,
    rule_id: str,
    payload: dict[str, Any],
    _admin: CurrentAdminAccount,
    service: AdminPaymentsServiceDep,
) -> dict[str, Any]:
    return await service.update_split_rule(account_id, rule_id, payload)


# -- Payments --------------------------------------------------


@router.get("/subaccounts/{account_id}/payments", summary="List a subaccount's charges")
async def list_payments(
    account_id: str,
    _admin: CurrentAdminAccount,
    service: AdminPaymentsServiceDep,
    status: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict[str, Any]:
    filters = {"status": status, "limit": limit, "offset": offset}
    clean_filters = {k: v for k, v in filters.items() if v is not None}
    return await service.list_payments(account_id, clean_filters)


@router.get("/subaccounts/{account_id}/payments/{payment_id}", summary="Retrieve a charge")
async def get_payment(
    account_id: str,
    payment_id: str,
    _admin: CurrentAdminAccount,
    service: AdminPaymentsServiceDep,
) -> dict[str, Any]:
    return await service.get_payment(account_id, payment_id)


@router.post(
    "/subaccounts/{account_id}/payments/{payment_id}/refund", summary="Refund a charge"
)
async def refund_payment(
    account_id: str,
    payment_id: str,
    _admin: CurrentAdminAccount,
    service: AdminPaymentsServiceDep,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await service.refund_payment(account_id, payment_id, payload or {})


@router.post(
    "/subaccounts/{account_id}/payments/{payment_id}/cancel", summary="Cancel a charge"
)
async def cancel_payment(
    account_id: str,
    payment_id: str,
    _admin: CurrentAdminAccount,
    service: AdminPaymentsServiceDep,
) -> dict[str, Any]:
    return await service.cancel_payment(account_id, payment_id)


# -- Audit --------------------------------------------------


@router.get("/asaas/audit-logs", summary="Global Asaas audit trail")
async def get_audit_logs(
    _admin: CurrentAdminAccount,
    service: AdminPaymentsServiceDep,
    client_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    filters = {"client_id": client_id, "limit": limit, "offset": offset}
    return await service.get_audit_logs({k: v for k, v in filters.items() if v is not None})
