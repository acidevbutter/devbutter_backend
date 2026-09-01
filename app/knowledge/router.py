import uuid

from fastapi import APIRouter, status

from app.accounts.dependencies import CurrentAdminAccount
from app.knowledge.dependencies import KnowledgeServiceDep
from app.knowledge.schemas import KnowledgeEntryCreate, KnowledgeEntryRead, KnowledgeEntryUpdate

router = APIRouter(prefix="/admin/knowledge-entries", tags=["knowledge"])


@router.get("", response_model=list[KnowledgeEntryRead])
async def list_entries(
    _admin: CurrentAdminAccount, service: KnowledgeServiceDep
) -> list[KnowledgeEntryRead]:
    entries = await service.list_all()
    return [KnowledgeEntryRead.model_validate(entry) for entry in entries]


@router.post("", response_model=KnowledgeEntryRead, status_code=status.HTTP_201_CREATED)
async def create_entry(
    payload: KnowledgeEntryCreate,
    _admin: CurrentAdminAccount,
    service: KnowledgeServiceDep,
) -> KnowledgeEntryRead:
    entry = await service.create(payload)
    return KnowledgeEntryRead.model_validate(entry)


@router.patch("/{entry_id}", response_model=KnowledgeEntryRead)
async def update_entry(
    entry_id: uuid.UUID,
    payload: KnowledgeEntryUpdate,
    _admin: CurrentAdminAccount,
    service: KnowledgeServiceDep,
) -> KnowledgeEntryRead:
    entry = await service.update(entry_id, payload)
    return KnowledgeEntryRead.model_validate(entry)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entry(
    entry_id: uuid.UUID,
    _admin: CurrentAdminAccount,
    service: KnowledgeServiceDep,
) -> None:
    await service.delete(entry_id)
