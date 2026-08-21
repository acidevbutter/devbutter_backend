import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.service_catalog.models import ServiceCatalogEntry
from app.service_catalog.schemas import ServiceCatalogEntryCreate, ServiceCatalogEntryUpdate


class ServiceCatalogRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, entry_id: uuid.UUID) -> ServiceCatalogEntry | None:
        return await self.session.get(ServiceCatalogEntry, entry_id)

    async def list_by_account(self, account_id: uuid.UUID) -> list[ServiceCatalogEntry]:
        result = await self.session.execute(
            select(ServiceCatalogEntry)
            .where(ServiceCatalogEntry.account_id == account_id)
            .order_by(ServiceCatalogEntry.created_at.desc())
        )
        return list(result.scalars())

    async def create(
        self, account_id: uuid.UUID, payload: ServiceCatalogEntryCreate
    ) -> ServiceCatalogEntry:
        entry = ServiceCatalogEntry(
            account_id=account_id, type=payload.type, description=payload.description
        )
        self.session.add(entry)
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    async def update(
        self, entry: ServiceCatalogEntry, payload: ServiceCatalogEntryUpdate
    ) -> ServiceCatalogEntry:
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(entry, field, value)
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    async def delete(self, entry: ServiceCatalogEntry) -> None:
        await self.session.delete(entry)
        await self.session.commit()
