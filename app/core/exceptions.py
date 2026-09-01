from collections.abc import Awaitable, Callable

from fastapi import Request, status
from fastapi.responses import JSONResponse

Handler = Callable[[Request, Exception], Awaitable[JSONResponse]]
_registry: dict[type[Exception], Handler] = {}


def exception_handler(exc_type: type[Exception]):
    """Decorator that registers a handler function for an exception type.
    Collect all of these onto the app with register_exception_handlers(app).
    """

    def decorator(func: Handler) -> Handler:
        _registry[exc_type] = func
        return func

    return decorator


def register_exception_handlers(app) -> None:
    for exc_type, handler in _registry.items():
        app.add_exception_handler(exc_type, handler)


class NotFoundError(Exception):
    def __init__(self, detail: str = "Resource not found"):
        self.detail = detail


class ConflictError(Exception):
    def __init__(self, detail: str = "Conflict"):
        self.detail = detail


class UnauthorizedError(Exception):
    def __init__(self, detail: str = "Unauthorized"):
        self.detail = detail


class ForbiddenError(Exception):
    def __init__(self, detail: str = "Forbidden"):
        self.detail = detail


class ValidationFailedError(Exception):
    def __init__(self, detail: str = "Validation failed"):
        self.detail = detail


class InvalidSecurePayloadError(Exception):
    """Any envelope/context/ownership/replay failure on the secure payment
    payload boundary (docs/prompts/payment-tokenization/runs/07-contract-freeze.md
    section 5.3). Deliberately carries no detail: bad kid, expired context,
    lost the anti-replay race, tenant/subject mismatch, or a failed AEAD tag
    must all produce the exact same response so nothing here can be used to
    distinguish which check failed.
    """


class UpstreamServiceError(Exception):
    """Raised when a core/clients/* call to asaas_payment, butter_mind, or
    butter_relay fails or returns an unexpected response. Kept distinct from
    ValidationFailedError so callers can tell "the client sent bad data" apart
    from "one of our own upstream services is down."
    """

    def __init__(
        self, detail: str = "An upstream service is unavailable", status_code: int | None = None
    ):
        self.detail = detail
        # The upstream HTTP status, when the failure was a >=400 response (vs. a
        # transport error). Not exposed by the handler -- callers use it to tell
        # e.g. "the butter_mind session is gone" (404) from a generic outage.
        self.status_code = status_code


class PaymentProviderUnavailableError(Exception):
    """Asaas/asaas_payment failed or timed out while tokenizing a card or
    charging a saved one (docs/prompts/payment-tokenization/runs/
    07-contract-freeze.md section 5.3, `payment_provider_unavailable`).
    Deliberately carries no upstream detail -- never echoes Asaas response
    bodies, which could contain card fragments or provider tokens.
    """


class InvoiceNotFoundError(Exception):
    """Invoice missing, or belonging to a different tenant than the
    authenticated caller (docs/prompts/payment-tokenization/runs/
    07-contract-freeze.md section 5.3, `invoice_not_found`). Deliberately
    carries no detail -- "doesn't exist" and "belongs to someone else" must
    look identical so existence can't be probed cross-tenant.
    """


class InvoiceNotPayableError(Exception):
    """Invoice already paid or cancelled (docs/prompts/payment-tokenization/
    runs/07-contract-freeze.md section 5.3, `invoice_not_payable`).
    """


class PaymentMethodNotFoundError(Exception):
    """Payment method missing, revoked in a way that makes it unusable, or
    belonging to a different tenant than the authenticated caller (docs/
    prompts/payment-tokenization/runs/07-contract-freeze.md section 5.3,
    `payment_method_not_found`). Same rationale as `InvoiceNotFoundError`:
    one response for every reason, so ownership can't be probed.
    """


class IdempotencyConflictError(Exception):
    """`idempotencyKey` reused with a different request body for the same
    invoice (docs/prompts/payment-tokenization/runs/07-contract-freeze.md
    section 5.3, `idempotency_conflict`).
    """


@exception_handler(NotFoundError)
async def handle_not_found(request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": exc.detail})


@exception_handler(ConflictError)
async def handle_conflict(request: Request, exc: ConflictError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": exc.detail})


@exception_handler(UnauthorizedError)
async def handle_unauthorized(request: Request, exc: UnauthorizedError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": exc.detail})


@exception_handler(ForbiddenError)
async def handle_forbidden(request: Request, exc: ForbiddenError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": exc.detail})


@exception_handler(ValidationFailedError)
async def handle_validation_failed(request: Request, exc: ValidationFailedError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": exc.detail}
    )


@exception_handler(InvalidSecurePayloadError)
async def handle_invalid_secure_payload(
    request: Request, exc: InvalidSecurePayloadError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": "invalid_secure_payload"},
        headers={"Cache-Control": "no-store"},
    )


@exception_handler(UpstreamServiceError)
async def handle_upstream_service_error(
    request: Request, exc: UpstreamServiceError
) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": exc.detail})


@exception_handler(PaymentProviderUnavailableError)
async def handle_payment_provider_unavailable(
    request: Request, exc: PaymentProviderUnavailableError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"error": "payment_provider_unavailable"},
        headers={"Cache-Control": "no-store"},
    )


@exception_handler(InvoiceNotFoundError)
async def handle_invoice_not_found(request: Request, exc: InvoiceNotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"error": "invoice_not_found"},
        headers={"Cache-Control": "no-store"},
    )


@exception_handler(InvoiceNotPayableError)
async def handle_invoice_not_payable(
    request: Request, exc: InvoiceNotPayableError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"error": "invoice_not_payable"},
        headers={"Cache-Control": "no-store"},
    )


@exception_handler(PaymentMethodNotFoundError)
async def handle_payment_method_not_found(
    request: Request, exc: PaymentMethodNotFoundError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"error": "payment_method_not_found"},
        headers={"Cache-Control": "no-store"},
    )


@exception_handler(IdempotencyConflictError)
async def handle_idempotency_conflict(
    request: Request, exc: IdempotencyConflictError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"error": "idempotency_conflict"},
        headers={"Cache-Control": "no-store"},
    )
