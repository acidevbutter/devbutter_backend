import uuid
from datetime import date
from decimal import Decimal

from app.accounts.repository import AccountRepository
from app.core.clients.asaas_client import AsaasClient
from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError
from app.invoices.repository import InvoiceRepository
from app.notifications.service import NotificationService
from app.orders.models import Order
from app.orders.repository import OrderRepository
from app.quotes.models import Quote


@log_errors
class OrderService:
    def __init__(
        self,
        repository: OrderRepository,
        account_repository: AccountRepository,
        invoice_repository: InvoiceRepository,
        asaas_client: AsaasClient,
        notification_service: NotificationService,
    ):
        self.repository = repository
        self.account_repository = account_repository
        self.invoice_repository = invoice_repository
        self.asaas_client = asaas_client
        self.notification_service = notification_service

    async def create_from_approved_quote(
        self, quote: Quote, *, amount: Decimal, due_date: date, billing_type: str
    ) -> Order:
        """Runs the Order->Asaas Customer->Asaas Invoice chain described in
        architecture doc §3 step 7. Everything here only runs after a human
        (admin) approval -- see quotes/service.py._require_pending and
        admin/router.py.
        """
        order = await self.repository.create(quote_id=quote.id, account_id=quote.account_id)

        account = await self.account_repository.get(quote.account_id)
        if account is None:
            raise NotFoundError(f"Account {quote.account_id} not found")
        company = await self.account_repository.get_company(account.company_id)
        if company is None:
            raise NotFoundError(f"Company {account.company_id} not found")
        user = await self.account_repository.get_user(company.user_id)
        if user is None:
            raise NotFoundError(f"User {company.user_id} not found")

        link = await self.account_repository.get_asaas_customer_link(company.id)
        if link is None:
            customer = await self.asaas_client.create_customer(
                {
                    "name": company.name,
                    "email": user.email,
                    "cpfCnpj": company.cnpj or user.cpf,
                    "mobilePhone": user.phone,
                }
            )
            link = await self.account_repository.create_asaas_customer_link(
                company.id, customer["id"]
            )

        payment = await self.asaas_client.create_payment(
            {
                "customer": link.asaas_customer_id,
                "billingType": billing_type,
                "value": float(amount),
                "dueDate": due_date.isoformat(),
                "description": quote.scope_summary[:500],
                "externalReference": str(order.id),
            }
        )

        invoice = await self.invoice_repository.create(
            order_id=order.id,
            asaas_invoice_id=payment["id"],
            amount=amount,
            due_date=due_date,
            status=payment.get("status", "PENDING"),
            payment_link=payment.get("invoiceUrl"),
        )

        order = await self.repository.set_status(order, "invoiced")
        self.notification_service.notify_quote_approved(user, quote, invoice)
        return order

    async def get(self, order_id: uuid.UUID) -> Order:
        order = await self.repository.get(order_id)
        if order is None:
            raise NotFoundError("Order not found")
        return order

    async def list_for_account(self, account_id: uuid.UUID) -> list[Order]:
        return await self.repository.list_by_account(account_id)
