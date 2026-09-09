from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.accounts.repository import (
    AccountRepository,
    AsaasSubaccountLinkRepository,
    CompanyBillingProfileRepository,
)
from app.core.clients.asaas_client import AsaasClient, get_asaas_client
from app.core.clients.blob_client import get_blob_client
from app.core.clients.relay_client import RelayClient, get_relay_client
from app.core.dependencies import DbSession
from app.invoices.repository import InvoiceRepository
from app.nfse.repository import NfseIssuanceRepository
from app.nfse.service import NfseService
from app.notifications.service import NotificationService
from app.orders.repository import OrderRepository
from app.payment_methods.repository import PaymentMethodRepository
from app.subaccounts_provisioning.service import SubaccountProvisioningService
from app.webhooks.repository import WebhookEventRepository
from app.webhooks.service import WebhookService


def get_webhook_service(
    db: DbSession,
    relay_client: Annotated[
        RelayClient,
        Depends(get_relay_client),
    ],
    asaas_client: Annotated[AsaasClient, Depends(get_asaas_client)],
) -> WebhookService:
    notification_service = NotificationService(relay_client)
    account_repository = AccountRepository(db)
    link_repository = AsaasSubaccountLinkRepository(db)
    provisioning_service = SubaccountProvisioningService(
        asaas_client,
        link_repository,
        CompanyBillingProfileRepository(db),
        account_repository,
        notification_service,
    )

    def build_nfse() -> NfseService:
        return NfseService(
            NfseIssuanceRepository(db),
            InvoiceRepository(db),
            OrderRepository(db),
            account_repository,
            asaas_client,
            get_blob_client(),
            relay_client,
        )

    return WebhookService(
        InvoiceRepository(db),
        OrderRepository(db),
        account_repository,
        notification_service,
        WebhookEventRepository(db),
        PaymentMethodRepository(db),
        nfse_factory=build_nfse,
        subaccount_link_repository=link_repository,
        provisioning_service=provisioning_service,
    )


WebhookServiceDep = Annotated[WebhookService, Depends(get_webhook_service)]
