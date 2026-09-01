from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.accounts.dependencies import CurrentAdminAccount
from app.accounts.models import AsaasSubaccountLink
from app.admin.dependencies import AdminCompaniesServiceDep, FeatureServiceDep
from app.admin.schemas import (
    AdminCompanyDetailRead,
    AdminCompanyFeatureRead,
    AdminCompanyListItem,
    AdminCompanyListRead,
    AdminLinkSubaccountRequest,
    AdminSplitQuotaUpdate,
    AdminSubaccountLinkRead,
)

router = APIRouter(prefix="/admin/companies", tags=["admin-companies"])


def _pagination(page: int, page_size: int) -> tuple[int, int, int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    return page, page_size, (page - 1) * page_size


def _link_read(link: AsaasSubaccountLink | None) -> AdminSubaccountLinkRead | None:
    if link is None:
        return None
    return AdminSubaccountLinkRead(
        asaas_account_id=link.asaas_account_id,
        wallet_id=link.wallet_id,
        onboarding_status=link.onboarding_status,
        approval_status=link.approval_status,
        split_quota_percent=link.split_quota_percent,
        quota_set_by=link.quota_set_by,
        quota_updated_at=link.quota_updated_at,
    )


@router.get("", response_model=AdminCompanyListRead, summary="List companies")
async def list_companies(
    _admin: CurrentAdminAccount,
    service: AdminCompaniesServiceDep,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> AdminCompanyListRead:
    page, page_size, offset = _pagination(page, page_size)
    rows, total = await service.list_companies(search=search, offset=offset, limit=page_size)
    return AdminCompanyListRead(
        items=[
            AdminCompanyListItem(
                id=row["company"].id,
                name=row["company"].name,
                cnpj=row["company"].cnpj,
                owner_name=row["user"].name,
                has_payment_method=row["link"] is not None,
                onboarding_status=row["link"].onboarding_status if row["link"] else None,
                approval_status=row["link"].approval_status if row["link"] else None,
                split_quota_percent=row["link"].split_quota_percent if row["link"] else None,
                used_percent=row["used_percent"],
            )
            for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{company_id}", response_model=AdminCompanyDetailRead, summary="Company detail")
async def get_company(
    company_id: uuid.UUID, _admin: CurrentAdminAccount, service: AdminCompaniesServiceDep
) -> AdminCompanyDetailRead:
    row = await service.get_company(company_id)
    return AdminCompanyDetailRead(
        id=row["company"].id,
        name=row["company"].name,
        cnpj=row["company"].cnpj,
        owner_name=row["user"].name,
        owner_email=row["user"].email,
        link=_link_read(row["link"]),
        split_rules=row["split_rules"],
        access_tokens=row["access_tokens"],
        used_percent=row["used_percent"],
    )


@router.patch(
    "/{company_id}/split-quota",
    response_model=AdminSubaccountLinkRead,
    summary="Set or clear a company's split quota",
)
async def set_split_quota(
    company_id: uuid.UUID,
    payload: AdminSplitQuotaUpdate,
    admin: CurrentAdminAccount,
    service: AdminCompaniesServiceDep,
) -> AdminSubaccountLinkRead:
    link = await service.set_split_quota(company_id, payload.split_quota_percent, admin.id)
    result = _link_read(link)
    assert result is not None
    return result


@router.post(
    "/{company_id}/link-subaccount",
    response_model=AdminSubaccountLinkRead,
    summary="Link an existing Asaas subaccount to a company",
)
async def link_subaccount(
    company_id: uuid.UUID,
    payload: AdminLinkSubaccountRequest,
    _admin: CurrentAdminAccount,
    service: AdminCompaniesServiceDep,
) -> AdminSubaccountLinkRead:
    link = await service.link_subaccount(company_id, payload.model_dump(exclude_none=True))
    result = _link_read(link)
    assert result is not None
    return result


@router.get(
    "/{company_id}/features",
    response_model=list[AdminCompanyFeatureRead],
    summary="List a company's feature toggles",
)
async def list_features(
    company_id: uuid.UUID, _admin: CurrentAdminAccount, service: FeatureServiceDep
) -> list[AdminCompanyFeatureRead]:
    features = await service.list_features(company_id)
    return [AdminCompanyFeatureRead.model_validate(f, from_attributes=True) for f in features]


@router.post(
    "/{company_id}/features/{feature_code}/enable",
    response_model=AdminCompanyFeatureRead,
    summary="Enable a feature for a company",
)
async def enable_feature(
    company_id: uuid.UUID,
    feature_code: str,
    admin: CurrentAdminAccount,
    service: FeatureServiceDep,
) -> AdminCompanyFeatureRead:
    feature = await service.enable(feature_code, company_id, admin.id)
    return AdminCompanyFeatureRead.model_validate(feature, from_attributes=True)


@router.post(
    "/{company_id}/features/{feature_code}/disable",
    response_model=AdminCompanyFeatureRead,
    summary="Disable a feature for a company",
)
async def disable_feature(
    company_id: uuid.UUID,
    feature_code: str,
    _admin: CurrentAdminAccount,
    service: FeatureServiceDep,
) -> AdminCompanyFeatureRead:
    feature = await service.disable(feature_code, company_id)
    return AdminCompanyFeatureRead.model_validate(feature, from_attributes=True)
