from app.accounts.models import Account
from app.core.clients.relay_client import RelayClient
from app.core.decorators import log_errors
from app.invoices.models import Invoice
from app.quotes.models import Quote

# Template keys registered in the butter_relay admin for the "devbutter-platform"
# project -- not hardcoded content here, only the payload each one needs.
_TEMPLATE_QUOTE_APPROVED = "devbutter_quote_approved"
_TEMPLATE_PAYMENT_STATUS_CHANGED = "devbutter_payment_status_changed"


@log_errors
class NotificationService:
    """Builds the payload and publishes it onto butter_relay's shared SQS
    queue. Never sends email itself and never talks to Brevo directly --
    see core/clients/relay_client.py and butter_relay/README.md.
    """

    def __init__(self, relay_client: RelayClient):
        self.relay_client = relay_client

    def notify_quote_approved(self, account: Account, quote: Quote, invoice: Invoice) -> None:
        self.relay_client.send(
            _TEMPLATE_QUOTE_APPROVED,
            account.email,
            {
                "name": account.name,
                "scope_summary": quote.scope_summary,
                "amount": str(invoice.amount),
                "due_date": invoice.due_date.isoformat(),
                "payment_link": invoice.payment_link,
            },
        )

    def notify_payment_status_changed(self, account: Account, invoice: Invoice) -> None:
        self.relay_client.send(
            _TEMPLATE_PAYMENT_STATUS_CHANGED,
            account.email,
            {
                "name": account.name,
                "status": invoice.status,
                "amount": str(invoice.amount),
                "payment_link": invoice.payment_link,
            },
        )
