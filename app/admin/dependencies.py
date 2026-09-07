from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.accounts.repository import (
    AccountRepository,
    AsaasSubaccountLinkRepository,
    CompanyBillingProfileRepository,
    CompanyFeatureRepository,
)
from app.admin.companies_service import AdminCompaniesService
from app.admin.feature_service import FeatureService
from app.admin.mind_service import AdminMindService
from app.admin.payments_service import AdminPaymentsService
from app.admin.repository import AdminRepository
from app.admin.service import AdminReadService, AdminService
from app.admin.subaccount_service import AdminSubaccountService
from app.conversations.repository import ConversationRepository
from app.core.clients.asaas_client import AsaasClient, get_asaas_client
from app.core.clients.butter_mind_client import ButterMindClient, get_butter_mind_client
from app.core.clients.relay_client import RelayClient, get_relay_client
from app.core.dependencies import DbSession
from app.invoices.repository import InvoiceRepository
from app.notifications.service import NotificationService
from app.orders.repository import OrderRepository
from app.orders.service import OrderService
from app.quotes.repository import QuoteRepository
from app.quotes.service import QuoteService
from app.subaccounts_provisioning.service import SubaccountProvisioningService


def get_admin_service(
    db: DbSession,
    asaas_client: Annotated[AsaasClient, Depends(get_asaas_client)],
    relay_client: Annotated[RelayClient, Depends(get_relay_client)],
) -> AdminService:
    quote_service = QuoteService(QuoteRepository(db))
    notification_service = NotificationService(relay_client)
    order_service = OrderService(
        OrderRepository(db),
        AccountRepository(db),
        InvoiceRepository(db),
        asaas_client,
        notification_service,
    )
    return AdminService(quote_service, order_service, AdminRepository(db))


AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]


def get_admin_read_service(db: DbSession) -> AdminReadService:
    return AdminReadService(QuoteService(QuoteRepository(db)), AdminRepository(db))


AdminReadServiceDep = Annotated[AdminReadService, Depends(get_admin_read_service)]


def get_admin_mind_service(
    db: DbSession,
    mind_client: Annotated[ButterMindClient, Depends(get_butter_mind_client)],
) -> AdminMindService:
    return AdminMindService(ConversationRepository(db), mind_client)


AdminMindServiceDep = Annotated[AdminMindService, Depends(get_admin_mind_service)]


def get_admin_payments_service(
    asaas_client: Annotated[AsaasClient, Depends(get_asaas_client)],
    db: DbSession,
) -> AdminPaymentsService:
    return AdminPaymentsService(asaas_client, AsaasSubaccountLinkRepository(db))


AdminPaymentsServiceDep = Annotated[AdminPaymentsService, Depends(get_admin_payments_service)]


def get_admin_companies_service(
    db: DbSession,
    payments_service: Annotated[AdminPaymentsService, Depends(get_admin_payments_service)],
) -> AdminCompaniesService:
    return AdminCompaniesService(
        AdminRepository(db),
        AsaasSubaccountLinkRepository(db),
        payments_service,
        CompanyBillingProfileRepository(db),
    )


AdminCompaniesServiceDep = Annotated[AdminCompaniesService, Depends(get_admin_companies_service)]


def get_feature_service(
    db: DbSession,
    payments_service: Annotated[AdminPaymentsService, Depends(get_admin_payments_service)],
) -> FeatureService:
    return FeatureService(
        AsaasSubaccountLinkRepository(db), CompanyFeatureRepository(db), payments_service
    )


FeatureServiceDep = Annotated[FeatureService, Depends(get_feature_service)]


def get_admin_subaccount_service(
    db: DbSession,
    asaas_client: Annotated[AsaasClient, Depends(get_asaas_client)],
    relay_client: Annotated[RelayClient, Depends(get_relay_client)],
) -> AdminSubaccountService:
    link_repository = AsaasSubaccountLinkRepository(db)
    provisioning_service = SubaccountProvisioningService(
        asaas_client,
        link_repository,
        CompanyBillingProfileRepository(db),
        AccountRepository(db),
        NotificationService(relay_client),
    )
    return AdminSubaccountService(link_repository, asaas_client, provisioning_service)


AdminSubaccountServiceDep = Annotated[
    AdminSubaccountService, Depends(get_admin_subaccount_service)
]
