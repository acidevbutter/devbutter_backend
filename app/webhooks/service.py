from typing import Any

from app.accounts.repository import AccountRepository
from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError
from app.invoices.repository import InvoiceRepository
from app.notifications.service import NotificationService
from app.orders.repository import OrderRepository
from app.payment_methods.repository import PaymentMethodRepository
from app.webhooks.models import WebhookEvent
from app.webhooks.repository import WebhookEventRepository
from app.webhooks.schemas import AsaasWebhookEvent

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
    ):
        self.invoice_repository = invoice_repository
        self.order_repository = order_repository
        self.account_repository = account_repository
        self.notification_service = notification_service
        self.event_repository = event_repository
        self.payment_method_repository = payment_method_repository

    async def process_event(
        self, event: AsaasWebhookEvent, raw_payload: dict[str, Any]
    ) -> WebhookEvent:
        invoice = await self.invoice_repository.get_by_asaas_invoice_id(event.payment.id)
        audit_event = await self.event_repository.create(
            event_type=event.event,
            related_invoice_id=invoice.id if invoice else None,
            payload=raw_payload,
        )
        try:
            await self.handle_payment_event(event)
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc) or exc.__class__.__name__
            await self.event_repository.mark_failed(audit_event, detail)
            raise
        return await self.event_repository.mark_processed(audit_event)

    async def reprocess_event(self, audit_event: WebhookEvent) -> WebhookEvent:
        event = AsaasWebhookEvent.model_validate(audit_event.payload)
        try:
            await self.handle_payment_event(event)
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc) or exc.__class__.__name__
            await self.event_repository.mark_failed(audit_event, detail)
            raise
        return await self.event_repository.mark_processed(audit_event)

    async def handle_payment_event(self, event: AsaasWebhookEvent) -> None:
        """Idempotent by construction: InvoiceRepository.update_status() is a
        no-op if the mapped status is already applied, so Asaas redelivering
        the same event twice sends at most one notification.
        """
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
