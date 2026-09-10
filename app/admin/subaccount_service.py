"""Admin operations on a Company's Asaas subaccount: manual provisioning
(retry), activation-link resend, key rotation, closing, and the annual
commercialInfo confirmation. Thin wrappers over ``AsaasClient`` /
``SubaccountProvisioningService`` -- the "Empresas" admin screen backs onto
this. Authorization is the router's ``CurrentAdminAccount`` gate.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from app.accounts.models import AsaasSubaccountLink
from app.accounts.repository import AsaasSubaccountLinkRepository
from app.core.clients.asaas_client import AsaasClient
from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError, UpstreamServiceError, ValidationFailedError
from app.subaccounts_provisioning.service import SubaccountProvisioningService


@log_errors
class AdminSubaccountService:
    def __init__(
        self,
        link_repository: AsaasSubaccountLinkRepository,
        asaas_client: AsaasClient,
        provisioning_service: SubaccountProvisioningService,
    ) -> None:
        self._link_repository = link_repository
        self._asaas_client = asaas_client
        self._provisioning_service = provisioning_service

    async def provision(
        self, company_id: uuid.UUID, admin_account_id: uuid.UUID
    ) -> AsaasSubaccountLink:
        link = await self._link_repository.get_by_company(company_id)
        if link is not None and link.approval_status == "APPROVED":
            raise ValidationFailedError("This company's subaccount is already approved")
        result = await self._provisioning_service.provision(
            company_id, actor=f"admin:{admin_account_id}"
        )
        return result.link

    async def _require_link(self, company_id: uuid.UUID) -> AsaasSubaccountLink:
        link = await self._link_repository.get_by_company(company_id)
        if link is None or link.asaas_account_id is None:
            raise NotFoundError("This company has no provisioned subaccount")
        return link

    async def resend_activation(self, company_id: uuid.UUID) -> dict[str, Any]:
        link = await self._require_link(company_id)
        return await self._asaas_client.resend_activation_link(cast(str, link.asaas_account_id))

    async def get_commercial_info(self, company_id: uuid.UUID) -> dict[str, Any]:
        link = await self._require_link(company_id)
        return await self._asaas_client.get_subaccount_commercial_info(
            cast(str, link.asaas_account_id)
        )

    async def update_commercial_info(
        self, company_id: uuid.UUID, payload: dict[str, Any]
    ) -> dict[str, Any]:
        link = await self._require_link(company_id)
        return await self._asaas_client.update_subaccount_commercial_info(
            cast(str, link.asaas_account_id), payload
        )

    async def rotate_key(self, company_id: uuid.UUID) -> dict[str, Any]:
        """Step 1 of the no-downtime rotation (asaas_subcontas_fluxo_completo.md
        §24): mint a fresh access token. The old key stays enabled until the
        operator confirms the new one is live and calls ``disable_key`` /
        ``delete_key``."""
        link = await self._require_link(company_id)
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M")
        try:
            return await self._asaas_client.create_access_token(
                cast(str, link.asaas_account_id), {"name": f"rotated-{stamp}"}
            )
        except UpstreamServiceError as exc:
            raise ValidationFailedError(
                "Asaas rejected the key creation -- confirm 'Gerenciamento de Chaves "
                "de API de Subcontas' is enabled (2h window) and the outbound IP is "
                f"whitelisted. ({exc})"
            ) from exc

    async def disable_key(
        self, company_id: uuid.UUID, token_id: str, expiration_date: str
    ) -> dict[str, Any]:
        link = await self._require_link(company_id)
        return await self._asaas_client.update_access_token(
            cast(str, link.asaas_account_id),
            token_id,
            {
                "name": f"disabled-{token_id[:8]}",
                "enabled": False,
                "expirationDate": expiration_date,
            },
        )

    async def delete_key(self, company_id: uuid.UUID, token_id: str) -> dict[str, Any]:
        link = await self._require_link(company_id)
        return await self._asaas_client.revoke_access_token(
            cast(str, link.asaas_account_id), token_id
        )

    async def close(self, company_id: uuid.UUID) -> AsaasSubaccountLink:
        link = await self._require_link(company_id)
        try:
            await self._asaas_client.close_subaccount(cast(str, link.asaas_account_id))
        except UpstreamServiceError as exc:
            # A BaaS subaccount can't be closed via DELETE /v3/accounts/{id} --
            # BaaS has its own process (asaas_subcontas_fluxo_completo.md §25/§27).
            raise ValidationFailedError(
                "Asaas refused to close this subaccount. If it is a BaaS subaccount, "
                "use Asaas's BaaS closure process (open a ticket) -- this route only "
                f"closes non-BaaS subaccounts. ({exc})"
            ) from exc
        link.provisioning_status = "CLOSED"
        link.closed_at = datetime.now(UTC)
        return await self._link_repository.update(link)
