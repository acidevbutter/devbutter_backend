from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.accounts.repository import AccountRepository
from app.core.clients.asaas_client import AsaasClient
from app.core.exceptions import (
    InvalidSecurePayloadError,
    InvoiceNotFoundError,
    InvoiceNotPayableError,
    PaymentMethodNotFoundError,
    PaymentProviderUnavailableError,
    UpstreamServiceError,
    ValidationFailedError,
)
from app.core.secure_payload import SecurePayloadError, key_from_base64url, validate_request_id
from app.invoices.models import Invoice
from app.invoices.repository import InvoiceRepository
from app.orders.repository import OrderRepository
from app.payment_methods.models import PaymentAttempt, PaymentMethod, PaymentSecurityContext
from app.payment_methods.repository import PaymentMethodRepository
from app.payment_methods.schemas import CardPayload, PaymentMethodPurpose
from app.payment_security.service import SecurePaymentContext, SecurePaymentPayloadService
from app.settings.config import settings

_METHOD = "POST"
_PATH = "/payment-methods"
_PURPOSE_CREATE_AND_PAY = "payment_method.create_and_pay"


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
        purpose: PaymentMethodPurpose,
        supported_kids: list[str],
        invoice_id: uuid.UUID | None,
    ) -> PaymentSecurityContext:
        """`POST /payment-methods/session` -- contract-07 sections 2.2 and 5.1.

        `tenant_id`/`subject_id` come exclusively from the authenticated
        session (the router's `CurrentAccount`/`CurrentCompany`
        dependencies), never from the request body. For
        `payment_method.create_and_pay`, `invoice_id`/`amount_minor`/
        `currency` are resolved here from the invoice's own database row
        (never from a client-supplied amount) and persisted on the ledger
        row; they are fixed for the life of the context (rule 3).
        """

        amount_minor: int | None = None
        currency: str | None = None
        if purpose == _PURPOSE_CREATE_AND_PAY:
            if invoice_id is None:  # pragma: no cover - schema already enforces this
                raise ValidationFailedError(
                    "invoiceId is required for payment_method.create_and_pay"
                )
            invoice = await self._owned_invoice(invoice_id, account_id)
            amount_minor = int((invoice.amount * 100).to_integral_value())
            currency = invoice.currency

        kid = self._select_kid(supported_kids)
        return await self._repository.create_context(
            PaymentSecurityContext(
                context_id=f"secctx_{secrets.token_urlsafe(24)}",
                account_id=account_id,
                company_id=company_id,
                purpose=purpose,
                kid=kid,
                allowed_method=_METHOD,
                allowed_path=_PATH,
                nonce=secrets.token_bytes(12),
                invoice_id=invoice_id,
                amount_minor=amount_minor,
                currency=currency,
                expires_at=datetime.now(UTC)
                + timedelta(seconds=settings.payment_context_ttl_seconds),
            )
        )

    async def create_from_envelope(
        self,
        *,
        account_id: uuid.UUID,
        company_id: uuid.UUID,
        envelope: dict[str, Any],
        remote_ip: str,
    ) -> tuple[PaymentMethod, PaymentAttempt | None]:
        """`POST /payment-methods` -- contract-07 sections 2.2, 3, 4 and 5.1.

        Body is exactly the v1 envelope, nothing else. Ownership is enforced
        by locating the ledger row through `contextId` and checking that the
        *authenticated request's* tenant/subject match the ones recorded at
        `POST /payment-methods/session` time (never trusting any value from
        the envelope's plaintext for that). The `unused -> processing ->
        consumed` transition brackets the decrypt attempt so a concurrent
        duplicate request can never observe or reuse the same context, and
        `processing -> consumed` always happens, success or failure, so a
        context can never be left stuck mid-flight.

        Every failure path here -- missing/malformed contextId, unknown
        context, tenant/subject mismatch, purpose/kid mismatch, lost the
        atomic-transition race, or the AEAD decrypt itself failing -- raises
        the same generic `invalid_secure_payload` (contract-07 section 5.3):
        callers must not be able to distinguish "wrong tenant" from "replay"
        from "bad ciphertext".
        """

        context_id = envelope.get("contextId")
        if not isinstance(context_id, str):
            raise InvalidSecurePayloadError()
        context = await self._repository.get_context(context_id)
        if (
            context is None
            or context.account_id != account_id
            or context.company_id != company_id
        ):
            raise InvalidSecurePayloadError()

        now = datetime.now(UTC)
        processing = await self._repository.begin_processing(context.context_id, now)
        if processing is None:
            # Covers: already consumed, expired, or lost the race against a
            # concurrent request for the same context_id -- exactly one of
            # two simultaneous callers ever gets past this point.
            raise InvalidSecurePayloadError()

        request_id_used: str | None = None
        failure_reason: str | None = None
        plaintext: dict[str, Any] | None = None
        card_payload: CardPayload | None = None
        try:
            envelope_kid = envelope.get("kid")
            if envelope_kid != processing.kid:
                raise SecurePayloadError("invalid_secure_payload")
            keyring = {
                kid: key_from_base64url(value)
                for kid, value in settings.payment_payload_keyring_dict.items()
            }
            plaintext = SecurePaymentPayloadService(keyring).decrypt_for_processing(
                envelope,
                context=SecurePaymentContext(
                    context_id=processing.context_id,
                    nonce=processing.nonce,
                    purpose=processing.purpose,
                    method=processing.allowed_method,
                    path=processing.allowed_path,
                    now=int(now.timestamp()),
                    tenant_id=str(processing.company_id),
                    subject_id=str(processing.account_id),
                    invoice_id=str(processing.invoice_id) if processing.invoice_id else None,
                    amount_minor=processing.amount_minor,
                    currency=processing.currency,
                ),
            )
            request_id_used = validate_request_id(str(envelope.get("requestId")))
            # Plaintext must be exactly the card/holder shape confirmed
            # against the real Asaas sandbox (schemas.CardPayload) -- a
            # malformed body is treated like any other crypto/replay
            # failure (contract-07 section 5.3), never a distinct
            # field-level validation error.
            try:
                card_payload = CardPayload.model_validate(plaintext)
            except ValidationError as exc:
                raise SecurePayloadError("invalid_secure_payload") from exc
            # Optional UX echo only (contract-07 section 2.2 rule 4) --
            # authorization always uses the context's own invoice_id, never
            # this plaintext value. A mismatch is treated exactly like any
            # other crypto/replay failure.
            if (
                processing.purpose == _PURPOSE_CREATE_AND_PAY
                and card_payload.invoice_id is not None
                and card_payload.invoice_id != str(processing.invoice_id)
            ):
                raise SecurePayloadError("invalid_secure_payload")
        except (SecurePayloadError, ValueError, KeyError) as exc:
            failure_reason = "decrypt_or_validation_failed"
            await self._finish_processing_safely(
                processing.context_id,
                request_id_used=None,
                failure_reason_code=failure_reason,
            )
            raise InvalidSecurePayloadError() from exc

        finished = await self._finish_processing_safely(
            processing.context_id, request_id_used=request_id_used, failure_reason_code=None
        )
        if finished is None:  # pragma: no cover - defensive, should be unreachable
            raise InvalidSecurePayloadError()

        if card_payload is None:  # pragma: no cover - defensive, should be unreachable
            raise InvalidSecurePayloadError()
        method = await self._tokenize_and_create_payment_method(
            company_id, card_payload, remote_ip
        )
        payment: PaymentAttempt | None = None
        if processing.purpose == _PURPOSE_CREATE_AND_PAY and processing.invoice_id is not None:
            payment = await self.pay(
                account_id=account_id,
                company_id=company_id,
                invoice_id=processing.invoice_id,
                payment_method_id=method.id,
                idempotency_key=f"context:{processing.context_id}",
            )
        return method, payment

    async def _finish_processing_safely(
        self,
        context_id: str,
        *,
        request_id_used: str | None,
        failure_reason_code: str | None,
    ) -> PaymentSecurityContext | None:
        """`processing -> consumed`, tolerating a `request_id_used` collision.

        `UNIQUE(account_id, request_id_used)` can raise `IntegrityError` if
        the same subject reused an envelope `requestId` across two
        contexts. That must never leave the row stuck in `processing`
        (contract-07 section 4, rule 2: the transition always happens), so
        on collision this retries once with `request_id_used=None` and a
        specific internal failure code.
        """

        try:
            return await self._repository.finish_processing(
                context_id,
                request_id_used=request_id_used,
                failure_reason_code=failure_reason_code,
                now=datetime.now(UTC),
            )
        except IntegrityError:
            return await self._repository.finish_processing(
                context_id,
                request_id_used=None,
                failure_reason_code="request_id_reused",
                now=datetime.now(UTC),
            )

    async def _tokenize_and_create_payment_method(
        self, company_id: uuid.UUID, card_payload: CardPayload, remote_ip: str
    ) -> PaymentMethod:
        """Real Asaas tokenization -- replaces the Phase-2 placeholder.

        Calls `asaas_payment`'s `POST /internal/payment-methods/tokenize`
        (`AsaasClient.tokenize_payment_method`), which forwards to Asaas's
        real `POST /creditCard/tokenizeCreditCard`. Field names/response
        shape below were confirmed against the live sandbox for this phase
        (see docs/prompts/payment-tokenization/runs/
        09-asaas-adapter-implementation.md) -- `creditCard`/
        `creditCardHolderInfo` go through almost verbatim (schemas.CardInput/
        CardHolderInfoInput already use Asaas's own field names), and the
        response is exactly `{creditCardNumber, creditCardBrand,
        creditCardToken}` with no expiry echoed back, so expiry is sourced
        from what the browser submitted.

        The plaintext card/holder dict is never logged: it is used once,
        passed straight into the mTLS call, and discarded when this method
        returns -- only the encrypted token and the non-sensitive brand/
        last4/expiry are ever persisted.
        """

        customer_id = await self._get_or_create_asaas_customer_id(company_id)
        card = card_payload.credit_card
        holder = card_payload.credit_card_holder_info
        payload = {
            "customer": customer_id,
            "creditCard": {
                "holderName": card.holder_name,
                "number": card.number,
                "expiryMonth": card.expiry_month,
                "expiryYear": card.expiry_year,
                "ccv": card.ccv,
            },
            "creditCardHolderInfo": {
                "name": holder.name,
                "email": holder.email,
                "cpfCnpj": holder.cpf_cnpj,
                "postalCode": holder.postal_code,
                "addressNumber": holder.address_number,
                "addressComplement": holder.address_complement,
                "phone": holder.phone,
            },
            "remoteIp": remote_ip,
        }
        try:
            response = await self._asaas.tokenize_payment_method(payload)
        except UpstreamServiceError as exc:
            raise PaymentProviderUnavailableError() from exc

        token = response.get("creditCardToken")
        brand = response.get("creditCardBrand")
        last4 = response.get("creditCardNumber")
        if not isinstance(token, str) or not token:
            # Asaas returned 2xx without a usable token -- treat exactly
            # like an upstream failure, never persist a method with no
            # real provider token.
            raise PaymentProviderUnavailableError()

        cipher = self._token_cipher()
        return await self._repository.create_method(
            PaymentMethod(
                company_id=company_id,
                asaas_customer_id=customer_id,
                provider_token_ciphertext=cipher.encrypt(token.encode()),
                token_kid=settings.payment_token_encryption_kid,
                brand=str(brand).lower() if brand else "unknown",
                last4=str(last4)[-4:] if last4 else card.number[-4:],
                expiry_month=int(card.expiry_month),
                expiry_year=int(card.expiry_year),
            )
        )

    async def _get_or_create_asaas_customer_id(self, company_id: uuid.UUID) -> str:
        """Same lazy-create-on-first-use pattern as `orders/service.py`
        (order approval) -- a card can be saved before any invoice/order
        exists (`payment_method.create`), so this path cannot assume the
        `AsaasCustomerLink` created there already exists.
        """

        link = await self._accounts.get_asaas_customer_link(company_id)
        if link is not None:
            return link.asaas_customer_id

        company = await self._accounts.get_company(company_id)
        if company is None:
            raise ValidationFailedError("Company not found")
        user = await self._accounts.get_user(company.user_id)
        if user is None:
            raise ValidationFailedError("User not found")

        try:
            customer = await self._asaas.create_customer(
                {
                    "name": company.name,
                    "email": user.email,
                    "cpfCnpj": company.cnpj or user.cpf,
                    "mobilePhone": user.phone,
                }
            )
        except UpstreamServiceError as exc:
            raise PaymentProviderUnavailableError() from exc

        link = await self._accounts.create_asaas_customer_link(company.id, customer["id"])
        return link.asaas_customer_id

    def _select_kid(self, supported_kids: list[str]) -> str:
        server_kids = settings.payment_payload_keyring_dict
        for kid in supported_kids:
            if kid in server_kids:
                return kid
        raise InvalidSecurePayloadError()

    async def list_methods(self, company_id: uuid.UUID) -> list[PaymentMethod]:
        return await self._repository.list_methods(company_id)

    async def revoke_method(
        self, company_id: uuid.UUID, payment_method_id: uuid.UUID
    ) -> PaymentMethod:
        method = await self._repository.get_method(payment_method_id)
        if method is None or method.company_id != company_id:
            # contract-07 section 5.3: `payment_method_not_found` (404) --
            # same response whether the id doesn't exist or belongs to
            # another tenant, so ownership can't be probed.
            raise PaymentMethodNotFoundError()
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
            # contract-07 section 5.3: `payment_method_not_found` (404) --
            # same response for "doesn't exist", "belongs to another
            # tenant" and "revoked", so none of those can be distinguished
            # from the outside.
            raise PaymentMethodNotFoundError()
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

    # Invoice.status values a webhook can set to something no longer
    # payable by card (app/webhooks/service.py `_STATUS_MAP`) -- contract-07
    # section 5.3 names this case literally as "invoice already paid or
    # cancelled".
    _UNPAYABLE_INVOICE_STATUSES = frozenset({"PAID", "CANCELLED"})

    async def _owned_invoice(self, invoice_id: uuid.UUID, account_id: uuid.UUID) -> Invoice:
        invoice = await self._invoices.get(invoice_id)
        if invoice is None:
            # contract-07 section 5.3: `invoice_not_found` (404) -- same
            # response whether the id doesn't exist or belongs to another
            # tenant (checked next), so existence can't be probed.
            raise InvoiceNotFoundError()
        order = await self._orders.get(invoice.order_id)
        if order is None or order.account_id != account_id:
            raise InvoiceNotFoundError()
        if invoice.status in self._UNPAYABLE_INVOICE_STATUSES:
            # contract-07 section 5.3: `invoice_not_payable` (409).
            raise InvoiceNotPayableError()
        if invoice.currency != "BRL" or invoice.amount <= 0:
            raise ValidationFailedError("Invoice is not eligible for card payment")
        return invoice

    @staticmethod
    def _token_cipher() -> Fernet:
        try:
            return Fernet(settings.payment_token_encryption_key.encode())
        except (TypeError, ValueError) as exc:
            raise ValidationFailedError("Payment token encryption is not configured") from exc
