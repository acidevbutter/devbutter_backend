import logging
from datetime import datetime
from typing import Any

from app.accounts.repository import (
    AccountRepository,
    AsaasSubaccountLinkRepository,
)
from app.core.decorators import log_errors
from app.core.exceptions import ConflictError, NotFoundError
from app.core.metrics import emit_metrics
from app.invoices.repository import InvoiceRepository
from app.notifications.service import NotificationService
from app.orders.repository import OrderRepository
from app.payment_methods.repository import PaymentMethodRepository
from app.subaccounts_provisioning.service import SubaccountProvisioningService
from app.webhooks.models import WebhookEvent
from app.webhooks.repository import WebhookEventRepository
from app.webhooks.schemas import AsaasWebhookEvent

logger = logging.getLogger(__name__)

# Maps the dimension segment of an ACCOUNT_STATUS_* event to the
# AsaasSubaccountLink column it updates.
_ACCOUNT_STATUS_DIMENSIONS = {
    "BANK_ACCOUNT_INFO": "bank_account_info_status",
    "COMMERCIAL_INFO": "commercial_info_status",
    "DOCUMENT": "documentation_status",
    "GENERAL_APPROVAL": "approval_status",
}

# Asaas payment status -> local Invoice.status. Unmapped statuses pass through
# unchanged so a new Asaas status doesn't silently get dropped.
_STATUS_MAP = {
    "PENDING": "PENDING",
    "RECEIVED": "PAID",
    "CONFIRMED": "PAID",
    "OVERDUE": "OVERDUE",
    "REFUNDED": "REFUNDED",
    "RECEIVED_IN_CASH": "PAID",
    "CHARGEBACK_REQUESTED": "CHARGEBACK",
    "CHARGEBACK_DISPUTE": "CHARGEBACK",
    "AWAITING_CHARGEBACK_REVERSAL": "CHARGEBACK",
    "DUNNING_REQUESTED": "OVERDUE",
    "DUNNING_RECEIVED": "PAID",
    "CANCELLED": "CANCELLED",
}


@log_errors
class WebhookService:
    def __init__(
        self,
        invoice_repository: InvoiceRepository,
        order_repository: OrderRepository,
        account_repository: AccountRepository,
        notification_service: NotificationService,
        event_repository: WebhookEventRepository,
        payment_method_repository: PaymentMethodRepository | None = None,
        subaccount_link_repository: AsaasSubaccountLinkRepository | None = None,
        provisioning_service: SubaccountProvisioningService | None = None,
    ):
        self.invoice_repository = invoice_repository
        self.order_repository = order_repository
        self.account_repository = account_repository
        self.notification_service = notification_service
        self.event_repository = event_repository
        self.payment_method_repository = payment_method_repository
        self.subaccount_link_repository = subaccount_link_repository
        self.provisioning_service = provisioning_service

    async def process_event(
        self, event: AsaasWebhookEvent, raw_payload: dict[str, Any]
    ) -> WebhookEvent:
        await emit_metrics({"EventType": event.event}, [("WebhookReceived", 1, "Count")])
        if event.id is not None:
            existing = await self.event_repository.get_by_event_id(event.id)
            if existing is not None:
                # "At least once" delivery -- Asaas can redeliver the same
                # event. Already recorded => accept and no-op.
                return existing
        invoice = (
            await self.invoice_repository.get_by_asaas_invoice_id(event.payment.id)
            if event.payment is not None
            else None
        )
        try:
            audit_event = await self.event_repository.create(
                event_type=event.event,
                related_invoice_id=invoice.id if invoice else None,
                payload=raw_payload,
                event_id=event.id,
            )
        except ConflictError:
            existing = (
                await self.event_repository.get_by_event_id(event.id)
                if event.id is not None
                else None
            )
            if existing is not None:
                return existing
            raise
        try:
            await self._dispatch(event)
        except Exception as exc:
            await emit_metrics({"EventType": event.event}, [("WebhookFailed", 1, "Count")])
            detail = getattr(exc, "detail", None) or str(exc) or exc.__class__.__name__
            await self.event_repository.mark_failed(audit_event, detail)
            raise
        processed = await self.event_repository.mark_processed(audit_event)
        await emit_metrics({"EventType": event.event}, [("WebhookProcessed", 1, "Count")])
        return processed

    async def reprocess_event(self, audit_event: WebhookEvent) -> WebhookEvent:
        event = AsaasWebhookEvent.model_validate(audit_event.payload)
        try:
            await self._dispatch(event)
        except Exception as exc:
            await emit_metrics({"EventType": event.event}, [("WebhookFailed", 1, "Count")])
            detail = getattr(exc, "detail", None) or str(exc) or exc.__class__.__name__
            await self.event_repository.mark_failed(audit_event, detail)
            raise
        processed = await self.event_repository.mark_processed(audit_event)
        await emit_metrics({"EventType": event.event}, [("WebhookProcessed", 1, "Count")])
        return processed

    async def _dispatch(self, event: AsaasWebhookEvent) -> None:
        """PAYMENT_* events go through the Invoice/Order reconciliation below.
        ACCOUNT_STATUS_* events update the local AsaasSubaccountLink mirror
        (onboarding/approval situation). Anything else is accepted, audited,
        and a no-op -- Asaas's "at least once" delivery and evolving payload
        rules mean unknown events must never fail the webhook
        (00-levantamento-tecnico.md item B7). Subaccount *credential*
        lifecycle stays with the asaas_payment proxy.
        """
        if event.event.startswith("ACCOUNT_STATUS_"):
            await self._handle_account_status_event(event)
            return
        if event.payment is not None:
            await self.handle_payment_event(event)

    async def _handle_account_status_event(self, event: AsaasWebhookEvent) -> None:
        if self.subaccount_link_repository is None:
            return
        account_id = (event.account or {}).get("id")
        if not account_id:
            return
        link = await self.subaccount_link_repository.get_by_asaas_account_id(str(account_id))
        if link is None:
            # A subaccount this backend didn't provision (another consumer).
            return

        suffix = event.event[len("ACCOUNT_STATUS_") :]
        new_status: str | None = None
        for dimension, column in _ACCOUNT_STATUS_DIMENSIONS.items():
            if suffix.startswith(dimension + "_"):
                new_status = suffix[len(dimension) + 1 :]
                setattr(link, column, new_status)
                break

        if event.event == "ACCOUNT_STATUS_GENERAL_APPROVAL_APPROVED":
            link.approval_status = "APPROVED"
            link.onboarding_status = "APPROVED"
        elif event.event == "ACCOUNT_STATUS_GENERAL_APPROVAL_REJECTED":
            link.onboarding_status = "REJECTED"
        elif event.event in {
            "ACCOUNT_STATUS_DOCUMENT_PENDING",
            "ACCOUNT_STATUS_DOCUMENT_REJECTED",
        }:
            link.onboarding_status = "PENDING_DOCUMENTS"
        elif event.event == "ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRING_SOON":
            link.onboarding_status = "COMMERCIAL_INFO_EXPIRING"
            link.commercial_info_expires_at = self._read_expiration(event)
            await self._notify_commercial_info_expiring(link)
        elif event.event == "ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRED":
            link.onboarding_status = "COMMERCIAL_INFO_EXPIRED"
            await self._notify_commercial_info_expiring(link)

        await self.subaccount_link_repository.update(link)
        await emit_metrics(
            {"EventType": event.event}, [("SubaccountStatusUpdated", 1, "Count")]
        )

    @staticmethod
    def _read_expiration(event: AsaasWebhookEvent) -> datetime | None:
        raw = (event.account or {}).get("commercialInfoExpiration") or {}
        scheduled = raw.get("scheduledDate") if isinstance(raw, dict) else None
        if not scheduled:
            return None
        try:
            return datetime.fromisoformat(str(scheduled).replace("Z", "+00:00"))
        except ValueError:
            return None

    async def _notify_commercial_info_expiring(self, link: Any) -> None:
        try:
            company = await self.account_repository.get_company(link.company_id)
            if company is None:
                return
            user = await self.account_repository.get_user(company.user_id)
            if user is None:
                return
            scheduled = (
                link.commercial_info_expires_at.isoformat()
                if link.commercial_info_expires_at
                else None
            )
            self.notification_service.notify_commercial_info_expiring(
                user, company.name, scheduled
            )
        except Exception:  # pragma: no cover - best effort
            logger.exception("commercial-info-expiring notification could not be queued")

    async def handle_payment_event(self, event: AsaasWebhookEvent) -> None:
        """Idempotent by construction: InvoiceRepository.update_status() is a
        no-op if the mapped status is already applied, so Asaas redelivering
        the same event twice sends at most one notification.
        """
        if event.payment is None:
            return
        invoice = await self.invoice_repository.get_by_asaas_invoice_id(event.payment.id)
        if invoice is None:
            raise NotFoundError(f"No invoice for Asaas payment {event.payment.id}")

        new_status = _STATUS_MAP.get(event.payment.status, event.payment.status)
        invoice, changed = await self.invoice_repository.update_status(invoice, new_status)
        await self._reconcile_payment_attempts(invoice.id, new_status)
        if not changed:
            return

        order = await self.order_repository.get(invoice.order_id)
        if order is None:
            return
        account = await self.account_repository.get(order.account_id)
        if account is None:
            return
        company = await self.account_repository.get_company(account.company_id)
        if company is None:
            return
        user = await self.account_repository.get_user(company.user_id)
        if user is None:
            return
        self.notification_service.notify_payment_status_changed(user, invoice)

        if new_status == "PAID":
            await self._maybe_provision_subaccount(company.id)

    async def _maybe_provision_subaccount(self, company_id: Any) -> None:
        """On a Company's first paid Layer A invoice, open its Asaas
        subaccount (docs/architecture/adr-0002 revised trigger). Idempotent:
        SubaccountProvisioningService no-ops when a link already exists, and
        we only reach here on the first PAID invoice anyway. Never raises --
        a provisioning problem must not fail the payment webhook."""
        if self.provisioning_service is None or self.subaccount_link_repository is None:
            return
        try:
            if await self.subaccount_link_repository.get_by_company(company_id) is not None:
                return
            if await self.invoice_repository.count_paid_invoices_for_company(company_id) > 1:
                return
            await self.provisioning_service.provision(
                company_id, actor="webhook:first_payment"
            )
        except Exception:
            logger.exception(
                "first-payment subaccount provisioning failed for company %s", company_id
            )

    async def _reconcile_payment_attempts(self, invoice_id: Any, new_status: str) -> None:
        """Real fix for Phase-3 item 6 (webhook wiring): a saved-card
        `PaymentAttempt` (payment_methods/service.py `pay()`) is created
        `processing`/`submitted`/`unknown` from Asaas's *synchronous*
        `payWithCreditCard` response -- this webhook is the async source of
        truth that eventually confirms it. Only advances attempts still
        `processing`/`submitted`/`unknown` to `succeeded` on a PAID mapping;
        idempotent (re-delivery just re-sets the same state) and never
        regresses an attempt already `succeeded`.
        """

        if self.payment_method_repository is None or new_status != "PAID":
            return
        for attempt in await self.payment_method_repository.list_attempts_by_invoice(invoice_id):
            if attempt.state in {"processing", "submitted", "unknown"}:
                await self.payment_method_repository.set_attempt_state(attempt, "succeeded")
