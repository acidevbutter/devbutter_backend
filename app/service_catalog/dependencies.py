from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import DbSession
from app.service_catalog.repository import ServiceCatalogRepository
from app.service_catalog.service import ServiceCatalogService


def get_service_catalog_service(db: DbSession) -> ServiceCatalogService:
    return ServiceCatalogService(ServiceCatalogRepository(db))


ServiceCatalogServiceDep = Annotated[ServiceCatalogService, Depends(get_service_catalog_service)]
