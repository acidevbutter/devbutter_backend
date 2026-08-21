import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.accounts.router import CurrentAccount
from app.core.dependencies import DbSession
from app.service_catalog.repository import ServiceCatalogRepository
from app.service_catalog.schemas import (
    ServiceCatalogEntryCreate,
    ServiceCatalogEntryRead,
    ServiceCatalogEntryUpdate,
)
from app.service_catalog.service import ServiceCatalogService


def get_service_catalog_service(db: DbSession) -> ServiceCatalogService:
    return ServiceCatalogService(ServiceCatalogRepository(db))


ServiceCatalogServiceDep = Annotated[ServiceCatalogService, Depends(get_service_catalog_service)]

router = APIRouter(prefix="/service-catalog", tags=["service-catalog"])


@router.get(
    "", response_model=list[ServiceCatalogEntryRead], summary="List the account's catalog entries"
)
async def list_entries(
    account: CurrentAccount, service: ServiceCatalogServiceDep
) -> list[ServiceCatalogEntryRead]:
    entries = await service.list_for_account(account.id)
    return [ServiceCatalogEntryRead.model_validate(e) for e in entries]


@router.post(
    "",
    response_model=ServiceCatalogEntryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a service/ecommerce catalog entry",
)
async def create_entry(
    payload: ServiceCatalogEntryCreate, account: CurrentAccount, service: ServiceCatalogServiceDep
) -> ServiceCatalogEntryRead:
    entry = await service.create(account.id, payload)
    return ServiceCatalogEntryRead.model_validate(entry)


@router.patch(
    "/{entry_id}", response_model=ServiceCatalogEntryRead, summary="Update a catalog entry"
)
async def update_entry(
    entry_id: uuid.UUID,
    payload: ServiceCatalogEntryUpdate,
    account: CurrentAccount,
    service: ServiceCatalogServiceDep,
) -> ServiceCatalogEntryRead:
    entry = await service.update(account.id, entry_id, payload)
    return ServiceCatalogEntryRead.model_validate(entry)


@router.delete(
    "/{entry_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a catalog entry"
)
async def delete_entry(
    entry_id: uuid.UUID, account: CurrentAccount, service: ServiceCatalogServiceDep
) -> None:
    await service.delete(account.id, entry_id)
