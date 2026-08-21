import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.models import KnowledgeEntry
from app.knowledge.schemas import KnowledgeEntryCreate, KnowledgeEntryUpdate


class KnowledgeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> list[KnowledgeEntry]:
        result = await self.session.execute(
            select(KnowledgeEntry).order_by(KnowledgeEntry.updated_at.desc())
        )
        return list(result.scalars())

    async def get(self, entry_id: uuid.UUID) -> KnowledgeEntry | None:
        return await self.session.get(KnowledgeEntry, entry_id)

    async def create(self, payload: KnowledgeEntryCreate) -> KnowledgeEntry:
        entry = KnowledgeEntry(**payload.model_dump())
        self.session.add(entry)
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    async def update(
        self, entry: KnowledgeEntry, payload: KnowledgeEntryUpdate
    ) -> KnowledgeEntry:
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(entry, field, value)
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    async def delete(self, entry: KnowledgeEntry) -> None:
        await self.session.delete(entry)
        await self.session.commit()
