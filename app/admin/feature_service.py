from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app.accounts.models import CompanyFeature
from app.accounts.repository import AsaasSubaccountLinkRepository, CompanyFeatureRepository
from app.admin.payments_service import AdminPaymentsService
from app.core.exceptions import NotFoundError, UpstreamServiceError, ValidationFailedError

_EnableHandler = Callable[[uuid.UUID, uuid.UUID], Awaitable[CompanyFeature]]
_DisableHandler = Callable[[uuid.UUID], Awaitable[CompanyFeature]]


class FeatureService:
    """Generic feature-toggle orchestration (docs/plano-feature-venda-via-api.md
    §4): the admin flips a feature on/off for a Company, and a per-feature
    handler decides what side effect that means. asaas_payment never learns
    what a "feature" or a "Company" is -- it only ever sees
    create_access_token/revoke_access_token calls for an account_id, same
    as any other admin-initiated token action.
    """

    def __init__(
        self,
        link_repository: AsaasSubaccountLinkRepository,
        feature_repository: CompanyFeatureRepository,
        payments_service: AdminPaymentsService,
    ):
        self._link_repository = link_repository
        self._feature_repository = feature_repository
        self._payments_service = payments_service
        self._handlers: dict[str, tuple[_EnableHandler, _DisableHandler]] = {
            "sells_via_api": (self._enable_sells_via_api, self._disable_sells_via_api),
        }

    async def list_features(self, company_id: uuid.UUID) -> list[CompanyFeature]:
        return await self._feature_repository.list_for_company(company_id)

    async def enable(
        self, feature_code: str, company_id: uuid.UUID, admin_user_id: uuid.UUID
    ) -> CompanyFeature:
        enable_fn, _ = self._handler_for(feature_code)
        return await enable_fn(company_id, admin_user_id)

    async def disable(self, feature_code: str, company_id: uuid.UUID) -> CompanyFeature:
        _, disable_fn = self._handler_for(feature_code)
        return await disable_fn(company_id)

    def _handler_for(self, feature_code: str) -> tuple[_EnableHandler, _DisableHandler]:
        handlers = self._handlers.get(feature_code)
        if handlers is None:
            raise NotFoundError(f"Unknown feature {feature_code!r}")
        return handlers

    async def _enable_sells_via_api(
        self, company_id: uuid.UUID, admin_user_id: uuid.UUID
    ) -> CompanyFeature:
        existing = await self._feature_repository.get_active(company_id, "sells_via_api")
        if existing is not None:
            return existing

        link = await self._link_repository.get_by_company(company_id)
        if link is None:
            raise ValidationFailedError("configure o meio de pagamento primeiro")

        record = await self._feature_repository.create(
            CompanyFeature(company_id=company_id, feature_code="sells_via_api", status="pending")
        )
        try:
            token = await self._payments_service.create_access_token(
                link.asaas_account_id, {"name": f"sells-via-api-{company_id}"}
            )
        except UpstreamServiceError as exc:
            record.status = "error"
            record.last_error = str(exc)
            await self._feature_repository.update(record)
            raise

        record.status = "active"
        record.asaas_access_token_id = token.get("id")
        record.enabled_by = admin_user_id
        record.enabled_at = datetime.now(UTC)
        return await self._feature_repository.update(record)

    async def _disable_sells_via_api(self, company_id: uuid.UUID) -> CompanyFeature:
        active = await self._feature_repository.get_active(company_id, "sells_via_api")
        if active is None:
            raise NotFoundError("sells_via_api is not active for this company")

        link = await self._link_repository.get_by_company(company_id)
        if link is not None and active.asaas_access_token_id:
            await self._payments_service.revoke_access_token(
                link.asaas_account_id, active.asaas_access_token_id
            )

        active.status = "disabled"
        active.disabled_at = datetime.now(UTC)
        return await self._feature_repository.update(active)
