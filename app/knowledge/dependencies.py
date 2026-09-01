from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import DbSession
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.service import KnowledgeService


def get_knowledge_service(db: DbSession) -> KnowledgeService:
    return KnowledgeService(KnowledgeRepository(db))


KnowledgeServiceDep = Annotated[KnowledgeService, Depends(get_knowledge_service)]
