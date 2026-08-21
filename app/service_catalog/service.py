import uuid

from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError
from app.service_catalog.models import ServiceCatalogEntry
from app.service_catalog.repository import ServiceCatalogRepository
from app.service_catalog.schemas import ServiceCatalogEntryCreate, ServiceCatalogEntryUpdate


@log_errors
class ServiceCatalogService:
    def __init__(self, repository: ServiceCatalogRepository):
        self.repository = repository

    async def list_for_account(self, account_id: uuid.UUID) -> list[ServiceCatalogEntry]:
        return await self.repository.list_by_account(account_id)

    async def create(
        self, account_id: uuid.UUID, payload: ServiceCatalogEntryCreate
    ) -> ServiceCatalogEntry:
        return await self.repository.create(account_id, payload)

    async def update(
        self, account_id: uuid.UUID, entry_id: uuid.UUID, payload: ServiceCatalogEntryUpdate
    ) -> ServiceCatalogEntry:
        entry = await self._get_owned(account_id, entry_id)
        return await self.repository.update(entry, payload)

    async def delete(self, account_id: uuid.UUID, entry_id: uuid.UUID) -> None:
        entry = await self._get_owned(account_id, entry_id)
        await self.repository.delete(entry)

    async def _get_owned(self, account_id: uuid.UUID, entry_id: uuid.UUID) -> ServiceCatalogEntry:
        entry = await self.repository.get(entry_id)
        if entry is None or entry.account_id != account_id:
            raise NotFoundError("Service catalog entry not found")
        return entry
