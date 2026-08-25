from __future__ import annotations

import base64
import ipaddress
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.accounts.repository import AccountRepository
from app.core.clients.asaas_client import AsaasClient
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    UpstreamServiceError,
    ValidationFailedError,
)
from app.core.secure_payload import SecurePayloadError, key_from_base64url, validate_request_id
from app.invoices.models import Invoice
from app.invoices.repository import InvoiceRepository
from app.orders.repository import OrderRepository
from app.payment_methods.models import PaymentAttempt, PaymentMethod, PaymentSecurityContext
from app.payment_methods.repository import PaymentMethodRepository
from app.payment_methods.schemas import CardPayload, PaymentMethodOperation
from app.payment_security.service import SecurePaymentContext, SecurePaymentPayloadService
from app.settings.config import settings

_PURPOSE = "payment_method.create"
_METHOD = "POST"
_PATH = "/payment-methods"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _brand(number: str) -> str:
    digits = "".join(char for char in number if char.isdigit())
    if digits.startswith("4"):
        return "visa"
    if digits[:2] in {"51", "52", "53", "54", "55"} or 2221 <= int(digits[:4] or 0) <= 2720:
        return "mastercard"
    if digits.startswith(("34", "37")):
        return "amex"
    if digits.startswith("6"):
        return "discover"
    return "card"


class PaymentMethodService:
    def __init__(
        self,
        repository: PaymentMethodRepository,
        invoice_repository: InvoiceRepository,
        order_repository: OrderRepository,
        account_repository: AccountRepository,
        asaas_client: AsaasClient,
    ):
        self._repository = repository
        self._invoices = invoice_repository
        self._orders = order_repository
        self._accounts = account_repository
        self._asaas = asaas_client

    async def create_context(
        self,
        *,
        account_id: uuid.UUID,
        company_id: uuid.UUID,
        invoice_id: uuid.UUID,
        operation: PaymentMethodOperation,
    ) -> PaymentSecurityContext:
        await self._owned_invoice(invoice_id, account_id)
        return await self._repository.create_context(
            PaymentSecurityContext(
                context_id=f"secctx_{secrets.token_urlsafe(24)}",
                account_id=account_id,
                company_id=company_id,
                invoice_id=invoice_id,
                operation=operation,
                nonce=secrets.token_bytes(12),
                expires_at=datetime.now(UTC)
                + timedelta(seconds=settings.payment_context_ttl_seconds),
            )
        )

    async def create_from_envelope(
        self,
        *,
        account_id: uuid.UUID,
        company_id: uuid.UUID,
        invoice_id: uuid.UUID,
        operation: PaymentMethodOperation,
        envelope: dict[str, object],
    ) -> PaymentMethod:
        context_id = envelope.get("contextId")
        if not isinstance(context_id, str):
            raise ValidationFailedError("invalid_secure_payload")
        context = await self._repository.get_context(context_id)
        if (
            context is None
            or context.account_id != account_id
            or context.company_id != company_id
            or context.invoice_id != invoice_id
            or context.operation != operation
        ):
            raise ValidationFailedError("invalid_secure_payload")
        try:
            keyring = {
                kid: key_from_base64url(value)
                for kid, value in settings.payment_payload_keyring_dict.items()
            }
            payload = SecurePaymentPayloadService(keyring).decrypt_for_processing(
                envelope,
                context=SecurePaymentContext(
                    context_id=context.context_id,
                    nonce=context.nonce,
                    purpose=_PURPOSE,
                    method=_METHOD,
                    path=_PATH,
                    now=int(datetime.now(UTC).timestamp()),
                ),
            )
            request_id = validate_request_id(str(envelope["requestId"]))
            card = CardPayload.model_validate(payload)
            ipaddress.ip_address(card.remote_ip)
        except (KeyError, SecurePayloadError, ValidationError, ValueError) as exc:
            raise ValidationFailedError("invalid_secure_payload") from exc

        consumed = await self._repository.consume_context(
            context.context_id, request_id, datetime.now(UTC)
        )
        if consumed is None:
            raise ConflictError("Payment submission context was already consumed or expired")

        customer_link = await self._accounts.get_asaas_customer_link(company_id)
        if customer_link is None:
            raise ValidationFailedError("Invoice customer is not configured for card payments")
        provider_response = await self._asaas.tokenize_payment_method(
            {
                "customer": customer_link.asaas_customer_id,
                "creditCard": {
                    "holderName": card.card.holder_name,
                    "number": card.card.number,
                    "expiryMonth": str(card.card.expiry_month),
                    "expiryYear": str(card.card.expiry_year),
                    "ccv": card.card.ccv,
                },
                "creditCardHolderInfo": {
                    "name": card.holder.name,
                    "email": card.holder.email,
                    "cpfCnpj": card.holder.cpf_cnpj,
                    "postalCode": card.holder.postal_code,
                    "addressNumber": card.holder.address_number,
                    "phone": card.holder.phone,
                    "mobilePhone": card.holder.mobile_phone,
                },
                "remoteIp": card.remote_ip,
            }
        )
        provider_token = provider_response.get("creditCardToken")
        if not isinstance(provider_token, str) or not provider_token:
            raise UpstreamServiceError("Asaas did not return a reusable card token")
        cipher = self._token_cipher()
        method = await self._repository.create_method(
            PaymentMethod(
                company_id=company_id,
                asaas_customer_id=customer_link.asaas_customer_id,
                provider_token_ciphertext=cipher.encrypt(provider_token.encode()),
                token_kid=settings.payment_token_encryption_kid,
                brand=_brand(card.card.number),
                last4="".join(char for char in card.card.number if char.isdigit())[-4:],
                expiry_month=card.card.expiry_month,
                expiry_year=card.card.expiry_year,
            )
        )
        if operation == "save_and_pay":
            await self.pay(
                account_id=account_id,
                company_id=company_id,
                invoice_id=invoice_id,
                payment_method_id=method.id,
                idempotency_key=f"context:{context.context_id}",
            )
        return method

    async def list_methods(self, company_id: uuid.UUID) -> list[PaymentMethod]:
        return await self._repository.list_methods(company_id)

    async def revoke_method(
        self, company_id: uuid.UUID, payment_method_id: uuid.UUID
    ) -> PaymentMethod:
        method = await self._repository.get_method(payment_method_id)
        if method is None or method.company_id != company_id:
            raise NotFoundError("Payment method not found")
        return await self._repository.revoke_method(method, datetime.now(UTC))

    async def pay(
        self,
        *,
        account_id: uuid.UUID,
        company_id: uuid.UUID,
        invoice_id: uuid.UUID,
        payment_method_id: uuid.UUID,
        idempotency_key: str,
    ) -> PaymentAttempt:
        invoice = await self._owned_invoice(invoice_id, account_id)
        method = await self._repository.get_method(payment_method_id)
        if method is None or method.company_id != company_id or method.status != "active":
            raise NotFoundError("Payment method not found")
        customer_link = await self._accounts.get_asaas_customer_link(company_id)
        if customer_link is None or method.asaas_customer_id != customer_link.asaas_customer_id:
            raise ValidationFailedError("Payment method is not valid for this customer")
        attempt = await self._repository.create_attempt(
            PaymentAttempt(
                invoice_id=invoice.id,
                payment_method_id=method.id,
                idempotency_key=idempotency_key,
            )
        )
        try:
            token = self._token_cipher().decrypt(method.provider_token_ciphertext).decode()
            response = await self._asaas.pay_with_saved_payment_method(
                invoice.asaas_invoice_id, token
            )
        except UpstreamServiceError:
            return await self._repository.set_attempt_state(
                attempt,
                "unknown",
                provider_payment_id=invoice.asaas_invoice_id,
                failure_code="upstream_unknown",
            )
        status = response.get("status") if isinstance(response, dict) else None
        return await self._repository.set_attempt_state(
            attempt,
            "succeeded" if status in {"RECEIVED", "CONFIRMED"} else "submitted",
            provider_payment_id=invoice.asaas_invoice_id,
        )

    async def reconcile_unresolved(self) -> int:
        reconciled = 0
        for attempt in await self._repository.list_unresolved_attempts():
            if not attempt.provider_payment_id:
                continue
            response = await self._asaas.retrieve_payment(attempt.provider_payment_id)
            status = response.get("status") if isinstance(response, dict) else None
            if status in {"RECEIVED", "CONFIRMED"}:
                await self._repository.set_attempt_state(attempt, "succeeded")
                reconciled += 1
            elif status:
                await self._repository.set_attempt_state(attempt, "submitted")
                reconciled += 1
        return reconciled

    async def _owned_invoice(self, invoice_id: uuid.UUID, account_id: uuid.UUID) -> Invoice:
        invoice = await self._invoices.get(invoice_id)
        if invoice is None:
            raise NotFoundError("Invoice not found")
        order = await self._orders.get(invoice.order_id)
        if order is None or order.account_id != account_id:
            raise NotFoundError("Invoice not found")
        if invoice.currency != "BRL" or invoice.amount <= 0:
            raise ValidationFailedError("Invoice is not eligible for card payment")
        return invoice

    @staticmethod
    def _token_cipher() -> Fernet:
        try:
            return Fernet(settings.payment_token_encryption_key.encode())
        except (TypeError, ValueError) as exc:
            raise ValidationFailedError("Payment token encryption is not configured") from exc
