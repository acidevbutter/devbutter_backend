from app.accounts.repository import AccountRepository
from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError
from app.invoices.repository import InvoiceRepository
from app.notifications.service import NotificationService
from app.orders.repository import OrderRepository
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
    ):
        self.invoice_repository = invoice_repository
        self.order_repository = order_repository
        self.account_repository = account_repository
        self.notification_service = notification_service

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
        if not changed:
            return

        order = await self.order_repository.get(invoice.order_id)
        if order is None:
            return
        account = await self.account_repository.get(order.account_id)
        if account is None:
            return
        self.notification_service.notify_payment_status_changed(account, invoice)
