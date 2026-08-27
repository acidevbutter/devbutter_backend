import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Frozen enum -- docs/prompts/payment-tokenization/runs/07-contract-freeze.md
# section 1. No other `purpose` value is valid for the envelope protocol in
# v1; paying with an already-saved method (`POST /payments`) is a distinct,
# non-enveloped endpoint (contract-07 section 0) and does not belong here.
PaymentMethodPurpose = Literal["payment_method.create", "payment_method.create_and_pay"]


class PaymentMethodSessionCreate(BaseModel):
    """`POST /payment-methods/session` request body -- contract-07 section 5.1.

    Field names are the contract's literal JSON: `purpose`, `supportedKids`,
    `invoiceId`. `invoiceId` is required for `payment_method.create_and_pay`
    and forbidden for `payment_method.create`.
    """

    model_config = ConfigDict(populate_by_name=True)

    purpose: PaymentMethodPurpose
    supported_kids: Annotated[list[str], Field(alias="supportedKids", min_length=1)]
    invoice_id: Annotated[uuid.UUID | None, Field(alias="invoiceId")] = None

    @model_validator(mode="after")
    def _invoice_id_matches_purpose(self) -> "PaymentMethodSessionCreate":
        needs_invoice = self.purpose == "payment_method.create_and_pay"
        if needs_invoice and self.invoice_id is None:
            raise ValueError("invoiceId is required for payment_method.create_and_pay")
        if not needs_invoice and self.invoice_id is not None:
            raise ValueError("invoiceId is not allowed for payment_method.create")
        return self


class PaymentMethodSessionRead(BaseModel):
    """`POST /payment-methods/session` response body -- contract-07 section 5.1.

    Never includes tenant_id/subject_id/invoice_id/amount_minor/currency --
    those stay server-side in the ledger row (contract-07 section 5.1,
    explicit note under the response example).
    """

    model_config = ConfigDict(populate_by_name=True)

    context_id: Annotated[str, Field(alias="contextId")]
    kid: str
    nonce: str
    expires_at: Annotated[int, Field(alias="expiresAt")]
    purpose: PaymentMethodPurpose
    protocol_version: Annotated[Literal[1], Field(alias="protocolVersion")] = 1


class PaymentMethodSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    brand: str
    last4: str
    expiry_month: Annotated[int, Field(alias="expiryMonth")]
    expiry_year: Annotated[int, Field(alias="expiryYear")]
    status: str


class PaymentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    state: str
    provider_payment_id: Annotated[str | None, Field(alias="providerPaymentId")]


class PaymentMethodCreateResponse(BaseModel):
    """`POST /payment-methods` 201 response body -- contract-07 section 5.1.

    `payment` is present only for `purpose == payment_method.create_and_pay`.
    """

    model_config = ConfigDict(populate_by_name=True)

    payment_method: Annotated[PaymentMethodSummary, Field(alias="paymentMethod")]
    payment: PaymentSummary | None = None


class PaymentMethodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand: str
    last4: str
    expiry_month: int
    expiry_year: int
    status: str
    created_at: datetime
    revoked_at: datetime | None


class PaymentCreate(BaseModel):
    """`POST /payments` request body.

    Deliberately snake_case on the wire, unlike every other class in this
    file. `POST /payments` (paying with an already-saved method) carries no
    card data and does not use the envelope/AAD protocol at all (contract-07
    section 0) -- it is authenticated by the portal session and
    `idempotencyKey`, not by anything in the AAD frame that forces a casing
    convention. `devbutter_frontend/src/lib/portal/payment-methods.ts`
    (`payWithPortalPaymentMethod`) has always sent snake_case here, and
    contract-07 section 5.1 has been amended to document that as the actual
    wire format instead of the originally-drafted (and never implemented)
    camelCase. `populate_by_name=True` plus the aliases below make this
    *also* accept camelCase (`invoiceId`/`paymentMethodId`/
    `idempotencyKey`) without narrowing what already works -- purely
    additive, so the existing frontend integration is untouched.
    """

    model_config = ConfigDict(populate_by_name=True)

    invoice_id: Annotated[uuid.UUID, Field(alias="invoiceId")]
    payment_method_id: Annotated[uuid.UUID, Field(alias="paymentMethodId")]
    idempotency_key: Annotated[
        str, Field(alias="idempotencyKey", min_length=16, max_length=128)
    ]


class PaymentRead(BaseModel):
    """`POST /payments` response body -- deliberately snake_case, unlike
    `PaymentSummary` (the sibling shape nested inside `POST /payment-methods`
    responses, which is genuinely camelCase on the wire). Adding a camelCase
    `Field(alias=...)` here would flip FastAPI's actual JSON output (this
    app's routes rely on the default `response_model_by_alias=True`), which
    would break `devbutter_frontend`'s `SavedPaymentResult` type
    (`provider_payment_id`) -- out of scope for this backend-only fix. See
    the `PaymentCreate` docstring above and contract-07 section 5.1 for the
    full rationale for why this one endpoint stays snake_case.
    """

    id: uuid.UUID
    state: str
    provider_payment_id: str | None


class CardInput(BaseModel):
    """Plaintext `creditCard` shape the browser encrypts inside the envelope.

    Field names are Asaas's own (`holderName`, `number`, `expiryMonth`,
    `expiryYear`, `ccv`) -- confirmed against the real sandbox
    `POST /creditCard/tokenizeCreditCard` call made for this phase (see
    `docs/prompts/payment-tokenization/runs/09-asaas-adapter-implementation.md`).
    Devbutter_backend forwards this dict to `asaas_payment` almost verbatim,
    only adding `customer`/`remoteIp` server-side -- this avoids inventing a
    translation layer for a shape Asaas already fixes.
    """

    model_config = ConfigDict(extra="forbid")

    holder_name: Annotated[str, Field(alias="holderName", min_length=1, max_length=128)]
    number: Annotated[str, Field(min_length=12, max_length=19, pattern=r"^\d{12,19}$")]
    # Digits-only pattern (not just min/max length) matters here: without it,
    # a well-typed but non-numeric expiryMonth/expiryYear (e.g. "ab") passes
    # this model and only blows up later at `int(card.expiry_month)` in
    # `PaymentMethodService._tokenize_and_create_payment_method` -- *outside*
    # the try/except that maps schema failures to the generic
    # `invalid_secure_payload` response (contract-07 section 5.3), producing
    # an unhandled 500 instead. Found during the F06-06 redaction audit
    # (docs/prompts/payment-tokenization/runs/10-redaction-proof.md); fixed
    # here so malformed expiry fails the schema validation path like any
    # other malformed card field, rather than reaching an unhandled
    # exception path at all.
    expiry_month: Annotated[str, Field(alias="expiryMonth", pattern=r"^\d{1,2}$")]
    expiry_year: Annotated[str, Field(alias="expiryYear", pattern=r"^\d{4}$")]
    ccv: Annotated[str, Field(min_length=3, max_length=4, pattern=r"^\d{3,4}$")]


class CardHolderInfoInput(BaseModel):
    """Plaintext `creditCardHolderInfo` shape -- same Asaas field names,
    same rationale as `CardInput`. `phone` and `address_complement` are the
    two optional fields Asaas itself treats as optional.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=128)]
    email: Annotated[str, Field(min_length=3, max_length=320)]
    cpf_cnpj: Annotated[str, Field(alias="cpfCnpj", min_length=11, max_length=18)]
    postal_code: Annotated[str, Field(alias="postalCode", min_length=8, max_length=9)]
    address_number: Annotated[str, Field(alias="addressNumber", min_length=1, max_length=16)]
    address_complement: Annotated[
        str | None, Field(alias="addressComplement", max_length=128)
    ] = None
    phone: Annotated[str | None, Field(max_length=30)] = None


class CardPayload(BaseModel):
    """Full plaintext body of the envelope for both `payment_method.create`
    and `payment_method.create_and_pay`. `invoiceId` is the optional UX
    echo contract-07 section 2.2 rule 4 describes -- never used for
    authorization, only compared against the ledger's own `invoice_id`.

    A plaintext that fails this schema is treated exactly like any other
    envelope/crypto failure (contract-07 section 5.3): the caller raises the
    same generic `invalid_secure_payload`, never a field-level validation
    error that could hint at what card data was rejected.
    """

    model_config = ConfigDict(extra="forbid")

    credit_card: Annotated[CardInput, Field(alias="creditCard")]
    credit_card_holder_info: Annotated[CardHolderInfoInput, Field(alias="creditCardHolderInfo")]
    invoice_id: Annotated[str | None, Field(alias="invoiceId")] = None
