import base64
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request, status

from app.accounts.repository import AccountRepository
from app.accounts.router import CurrentAccount, CurrentAdminAccount, CurrentCompany
from app.core.clients.asaas_client import AsaasClient, get_asaas_client
from app.core.dependencies import DbSession
from app.invoices.repository import InvoiceRepository
from app.orders.repository import OrderRepository
from app.payment_methods.repository import PaymentMethodRepository
from app.payment_methods.schemas import (
    PaymentCreate,
    PaymentMethodCreateResponse,
    PaymentMethodRead,
    PaymentMethodSessionCreate,
    PaymentMethodSessionRead,
    PaymentMethodSummary,
    PaymentRead,
    PaymentSummary,
)
from app.payment_methods.service import PaymentMethodService


def get_payment_method_service(
    db: DbSession, asaas_client: Annotated[AsaasClient, Depends(get_asaas_client)]
) -> PaymentMethodService:
    return PaymentMethodService(
        PaymentMethodRepository(db),
        InvoiceRepository(db),
        OrderRepository(db),
        AccountRepository(db),
        asaas_client,
    )


PaymentMethodServiceDep = Annotated[PaymentMethodService, Depends(get_payment_method_service)]
router = APIRouter(tags=["payment-methods"])


@router.post(
    "/payment-methods/session",
    response_model=PaymentMethodSessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_payment_method_session(
    payload: PaymentMethodSessionCreate,
    account: CurrentAccount,
    company: CurrentCompany,
    service: PaymentMethodServiceDep,
) -> PaymentMethodSessionRead:
    context = await service.create_context(
        account_id=account.id,
        company_id=company.id,
        purpose=payload.purpose,
        supported_kids=payload.supported_kids,
        invoice_id=payload.invoice_id,
    )
    return PaymentMethodSessionRead(
        context_id=context.context_id,
        kid=context.kid,
        nonce=base64.urlsafe_b64encode(context.nonce).decode().rstrip("="),
        expires_at=int(context.expires_at.timestamp()),
        purpose=context.purpose,
    )


@router.post(
    "/payment-methods",
    response_model=PaymentMethodCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_payment_method(
    request: Request,
    account: CurrentAccount,
    company: CurrentCompany,
    service: PaymentMethodServiceDep,
    envelope: Annotated[dict[str, Any], Body()],
) -> PaymentMethodCreateResponse:
    # Contract-07 section 5.1: the request body IS the v1 envelope, no
    # wrapper fields (invoiceId/purpose are already bound server-side to the
    # ledger row located by the envelope's own contextId).
    remote_ip = request.client.host if request.client else "0.0.0.0"
    method, payment = await service.create_from_envelope(
        account_id=account.id,
        company_id=company.id,
        envelope=envelope,
        remote_ip=remote_ip,
    )
    return PaymentMethodCreateResponse(
        payment_method=PaymentMethodSummary.model_validate(method),
        payment=PaymentSummary.model_validate(payment) if payment is not None else None,
    )


@router.get("/payment-methods", response_model=list[PaymentMethodRead])
async def list_payment_methods(
    company: CurrentCompany, service: PaymentMethodServiceDep
) -> list[PaymentMethodRead]:
    return [
        PaymentMethodRead.model_validate(item) for item in await service.list_methods(company.id)
    ]


@router.delete("/payment-methods/{payment_method_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_payment_method(
    payment_method_id: uuid.UUID, company: CurrentCompany, service: PaymentMethodServiceDep
) -> None:
    await service.revoke_method(company.id, payment_method_id)


@router.post("/payments", response_model=PaymentRead, status_code=status.HTTP_202_ACCEPTED)
async def create_payment(
    payload: PaymentCreate,
    account: CurrentAccount,
    company: CurrentCompany,
    service: PaymentMethodServiceDep,
) -> PaymentRead:
    attempt = await service.pay(
        account_id=account.id,
        company_id=company.id,
        invoice_id=payload.invoice_id,
        payment_method_id=payload.payment_method_id,
        idempotency_key=payload.idempotency_key,
    )
    return PaymentRead(
        id=attempt.id,
        state=attempt.state,
        provider_payment_id=attempt.provider_payment_id,
    )


@router.post("/admin/payment-methods/reconcile", status_code=status.HTTP_202_ACCEPTED)
async def reconcile_payment_attempts(
    _admin: CurrentAdminAccount, service: PaymentMethodServiceDep
) -> dict[str, int]:
    return {"reconciled": await service.reconcile_unresolved()}
