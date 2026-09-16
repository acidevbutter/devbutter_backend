from datetime import datetime
from typing import Any

from app.accounts.models import User
from app.core.clients.relay_client import RelayClient
from app.core.decorators import log_errors
from app.invoices.models import Invoice
from app.quotes.models import Quote
from app.settings.config import settings

# Template keys registered in the butter_relay admin for the "devbutter-platform"
# project -- not hardcoded content here, only the payload each one needs.
_TEMPLATE_QUOTE_APPROVED = "devbutter_quote_approved"
_TEMPLATE_PAYMENT_STATUS_CHANGED = "devbutter_payment_status_changed"
_TEMPLATE_PENDING_ACCOUNT_LINK = "devbutter_pending_account_link"
_TEMPLATE_QUOTE_RECEIVED = "devbutter_quote_received"
_TEMPLATE_PASSWORD_RECOVERY = "devbutter_password_recovery"
_TEMPLATE_SUBACCOUNT_ONBOARDING = "devbutter_subaccount_onboarding"
_TEMPLATE_COMMERCIAL_INFO_EXPIRING = "devbutter_commercial_info_expiring"


@log_errors
class NotificationService:
    """Builds the payload and publishes it onto butter_relay's shared Celery
    queue (Redis broker). Never sends email itself and never talks to Brevo
    directly -- see core/clients/relay_client.py and butter_relay/README.md.
    """

    def __init__(self, relay_client: RelayClient):
        self.relay_client = relay_client

    def notify_quote_approved(self, user: User, quote: Quote, invoice: Invoice) -> None:
        self.relay_client.send(
            _TEMPLATE_QUOTE_APPROVED,
            user.email,
            {
                "name": user.name,
                "scope_summary": quote.scope_summary,
                "amount": str(invoice.amount),
                "due_date": invoice.due_date.isoformat(),
                "payment_link": invoice.payment_link,
            },
        )

    def notify_pending_account_link(self, user: User, extraction: dict[str, Any]) -> None:
        """An anonymous visitor finished a chat using this User's email, but
        was not auto-logged into their account (see
        docs/plano-onboarding-conversa-primeiro.md §3.1) -- lets the account
        owner know a draft quote was saved under their account.
        """
        self.relay_client.send(
            _TEMPLATE_PENDING_ACCOUNT_LINK,
            user.email,
            {
                "name": user.name,
                "scope_summary": extraction.get("problem_summary"),
            },
        )

    def notify_quote_received(
        self, user: User, quote: Quote, password_setup_token: str | None
    ) -> None:
        """A brand-new account was created on the spot from a visitor's chat
        (Fluxo Cotação IA canvas screens 1e-1f) -- confirms the draft quote
        and, when the visitor didn't type a password inline,
        `password_setup_token` carries the link that lets them set one.
        """
        password_setup_url = (
            f"{settings.frontend_base_url}/portal/senha/{password_setup_token}"
            if password_setup_token
            else None
        )
        self.relay_client.send(
            _TEMPLATE_QUOTE_RECEIVED,
            user.email,
            {
                "name": user.name,
                "quote_reference": quote.reference,
                "scope_summary": quote.scope_summary,
                "price_range": quote.price_range,
                "timeline": quote.timeline,
                "status_label": "aguardando revisão",
                "password_setup_url": password_setup_url,
                "portal_url": f"{settings.frontend_base_url}/portal",
            },
        )

    def notify_password_recovery(
        self, user: User, token: str, expires_at: datetime
    ) -> None:
        self.relay_client.send(
            _TEMPLATE_PASSWORD_RECOVERY,
            user.email,
            {
                "name": user.name,
                "password_recovery_url": (
                    f"{settings.frontend_base_url}/portal/recuperar-senha/{token}"
                ),
                "expires_at": expires_at.isoformat(),
            },
        )

    def notify_subaccount_onboarding(self, user: User, company_name: str) -> None:
        """The company's Asaas subaccount was just provisioned -- point the
        owner at the portal onboarding screen (login-gated) rather than a raw
        Asaas onboardingUrl, which isn't available yet at this moment."""
        self.relay_client.send(
            _TEMPLATE_SUBACCOUNT_ONBOARDING,
            user.email,
            {
                "name": user.name,
                "company": company_name,
                "onboarding_url": f"{settings.frontend_base_url}/portal/recebimentos",
            },
        )

    def notify_commercial_info_expiring(
        self, user: User, company_name: str, scheduled_date: str | None
    ) -> None:
        self.relay_client.send(
            _TEMPLATE_COMMERCIAL_INFO_EXPIRING,
            user.email,
            {
                "name": user.name,
                "company": company_name,
                "scheduled_date": scheduled_date,
                "onboarding_url": f"{settings.frontend_base_url}/portal/recebimentos",
            },
        )

    def notify_payment_status_changed(self, user: User, invoice: Invoice) -> None:
        self.relay_client.send(
            _TEMPLATE_PAYMENT_STATUS_CHANGED,
            user.email,
            {
                "name": user.name,
                "status": invoice.status,
                "amount": str(invoice.amount),
                "payment_link": invoice.payment_link,
            },
        )
