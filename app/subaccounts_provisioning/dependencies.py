from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.accounts.repository import (
    AccountRepository,
    AsaasSubaccountLinkRepository,
    CompanyBillingProfileRepository,
)
from app.core.clients.asaas_client import AsaasClient, get_asaas_client
from app.core.dependencies import DbSession
from app.notifications.dependencies import get_notification_service
from app.notifications.service import NotificationService
from app.subaccounts_provisioning.service import SubaccountProvisioningService


def get_subaccount_provisioning_service(
    db: DbSession,
    asaas_client: Annotated[AsaasClient, Depends(get_asaas_client)],
    notification_service: Annotated[NotificationService, Depends(get_notification_service)],
) -> SubaccountProvisioningService:
    return SubaccountProvisioningService(
        asaas_client,
        AsaasSubaccountLinkRepository(db),
        CompanyBillingProfileRepository(db),
        AccountRepository(db),
        notification_service,
    )


SubaccountProvisioningServiceDep = Annotated[
    SubaccountProvisioningService, Depends(get_subaccount_provisioning_service)
]
