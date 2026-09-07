from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.accounts.repository import AccountRepository
from app.core.clients.asaas_client import AsaasClient, get_asaas_client
from app.core.clients.blob_client import BlobClient, get_blob_client
from app.core.clients.relay_client import RelayClient, get_relay_client
from app.core.dependencies import DbSession
from app.invoices.repository import InvoiceRepository
from app.nfse.repository import NfseIssuanceRepository
from app.nfse.service import NfseService
from app.orders.repository import OrderRepository


def get_nfse_service(
    db: DbSession,
    asaas_client: Annotated[AsaasClient, Depends(get_asaas_client)],
    blob_client: Annotated[BlobClient, Depends(get_blob_client)],
    relay_client: Annotated[RelayClient, Depends(get_relay_client)],
) -> NfseService:
    return NfseService(
        NfseIssuanceRepository(db),
        InvoiceRepository(db),
        OrderRepository(db),
        AccountRepository(db),
        asaas_client,
        blob_client,
        relay_client,
    )


NfseServiceDep = Annotated[NfseService, Depends(get_nfse_service)]
