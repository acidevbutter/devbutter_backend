"""Client-facing (portal) operations around a Company's Asaas subaccount: the
billing profile it submits, and reading/advancing its BaaS onboarding. Kept
separate from ``AccountService`` (which every authenticated request resolves)
so the mTLS ``AsaasClient`` isn't dragged into auth resolution.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.accounts.models import AsaasSubaccountLink, CompanyBillingProfile
from app.accounts.repository import (
    AsaasSubaccountLinkRepository,
    CompanyBillingProfileRepository,
)
from app.accounts.schemas import BillingProfileInput
from app.core.clients.asaas_client import AsaasClient
from app.core.decorators import log_errors
from app.core.exceptions import UpstreamServiceError, ValidationFailedError

_APPROVED = "APPROVED"


def onboarding_required(link: AsaasSubaccountLink | None) -> bool:
    """The portal blocks everything but /portal/recebimentos while this is
    true: a subaccount exists but Asaas hasn't approved it yet."""
    if link is None:
        return False
    return link.provisioning_status == "PROVISIONED" and link.approval_status != _APPROVED


@log_errors
class AccountBillingService:
    def __init__(
        self,
        billing_profile_repository: CompanyBillingProfileRepository,
        link_repository: AsaasSubaccountLinkRepository,
        asaas_client: AsaasClient,
    ) -> None:
        self._billing_profile_repository = billing_profile_repository
        self._link_repository = link_repository
        self._asaas_client = asaas_client

    async def get_profile(self, company_id: uuid.UUID) -> CompanyBillingProfile | None:
        return await self._billing_profile_repository.get_by_company(company_id)

    async def upsert_profile(
        self, company_id: uuid.UUID, payload: BillingProfileInput
    ) -> CompanyBillingProfile:
        return await self._billing_profile_repository.upsert(company_id, payload)

    async def get_link(self, company_id: uuid.UUID) -> AsaasSubaccountLink | None:
        return await self._link_repository.get_by_company(company_id)

    async def get_onboarding(self, company_id: uuid.UUID) -> dict[str, Any]:
        link = await self._link_repository.get_by_company(company_id)
        if link is None or link.asaas_account_id is None:
            return {"state": "not_provisioned"}

        documents = await self._asaas_client.get_subaccount_onboarding_documents(
            link.asaas_account_id
        )
        status = await self._asaas_client.get_subaccount_onboarding_status(
            link.asaas_account_id
        )
        pending = _extract_pending_documents(documents)
        onboarding_url = next(
            (doc["onboardingUrl"] for doc in pending if doc.get("onboardingUrl")), None
        )
        return {
            "state": link.onboarding_status or "AWAITING_ONBOARDING",
            "general": status.get("general"),
            "commercialInfo": status.get("commercialInfo"),
            "bankAccountInfo": status.get("bankAccountInfo"),
            "documentation": status.get("documentation"),
            "pending_documents": pending,
            "onboarding_url": onboarding_url,
        }

    async def get_commercial_info(self, company_id: uuid.UUID) -> dict[str, Any]:
        account_id = await self._require_account_id(company_id)
        return await self._asaas_client.get_subaccount_commercial_info(account_id)

    async def update_commercial_info(
        self, company_id: uuid.UUID, payload: dict[str, Any]
    ) -> dict[str, Any]:
        account_id = await self._require_account_id(company_id)
        return await self._asaas_client.update_subaccount_commercial_info(account_id, payload)

    async def resend_activation(self, company_id: uuid.UUID) -> dict[str, Any]:
        account_id = await self._require_account_id(company_id)
        return await self._asaas_client.resend_activation_link(account_id)

    async def upload_document(
        self,
        company_id: uuid.UUID,
        group_id: str,
        *,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> dict[str, Any]:
        account_id = await self._require_account_id(company_id)
        return await self._asaas_client.upload_subaccount_document(
            account_id,
            group_id,
            filename=filename,
            content=content,
            content_type=content_type,
        )

    async def _require_account_id(self, company_id: uuid.UUID) -> str:
        link = await self._link_repository.get_by_company(company_id)
        if link is None or link.asaas_account_id is None:
            raise ValidationFailedError("This company has no provisioned subaccount")
        return link.asaas_account_id


def _extract_pending_documents(documents: dict[str, Any]) -> list[dict[str, Any]]:
    data = documents.get("data") if isinstance(documents, dict) else None
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for group in data:
        if not isinstance(group, dict):
            continue
        if str(group.get("status", "")).upper() == "APPROVED":
            continue
        out.append(
            {
                "id": group.get("id"),
                "type": group.get("type"),
                "status": group.get("status"),
                "title": group.get("title") or group.get("description"),
                "onboardingUrl": group.get("onboardingUrl"),
            }
        )
    return out


__all__ = [
    "AccountBillingService",
    "UpstreamServiceError",
    "onboarding_required",
]
