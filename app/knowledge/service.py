import uuid
from decimal import Decimal

from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.knowledge.models import KnowledgeEntry
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.schemas import KnowledgeEntryCreate, KnowledgeEntryUpdate


@log_errors
class KnowledgeService:
    def __init__(self, repository: KnowledgeRepository):
        self.repository = repository

    async def list_all(self) -> list[KnowledgeEntry]:
        return await self.repository.list_all()

    async def create(self, payload: KnowledgeEntryCreate) -> KnowledgeEntry:
        self._validate_range(payload.price_min, payload.price_max)
        return await self.repository.create(payload)

    async def update(
        self, entry_id: uuid.UUID, payload: KnowledgeEntryUpdate
    ) -> KnowledgeEntry:
        entry = await self.repository.get(entry_id)
        if entry is None:
            raise NotFoundError("Knowledge entry not found")
        price_min = payload.price_min if payload.price_min is not None else entry.price_min
        price_max = payload.price_max if payload.price_max is not None else entry.price_max
        self._validate_range(price_min, price_max)
        return await self.repository.update(entry, payload)

    async def delete(self, entry_id: uuid.UUID) -> None:
        entry = await self.repository.get(entry_id)
        if entry is None:
            raise NotFoundError("Knowledge entry not found")
        await self.repository.delete(entry)

    @staticmethod
    def _validate_range(price_min: Decimal, price_max: Decimal) -> None:
        if price_min < 0 or price_max < 0 or price_min > price_max:
            raise ValidationFailedError("price_min must be less than or equal to price_max")
