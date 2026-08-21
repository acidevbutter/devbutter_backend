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


class UpstreamServiceError(Exception):
    """Raised when a core/clients/* call to asaas_payment, butter_mind, or
    butter_relay fails or returns an unexpected response. Kept distinct from
    ValidationFailedError so callers can tell "the client sent bad data" apart
    from "one of our own upstream services is down."
    """

    def __init__(self, detail: str = "An upstream service is unavailable"):
        self.detail = detail


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


@exception_handler(UpstreamServiceError)
async def handle_upstream_service_error(
    request: Request, exc: UpstreamServiceError
) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": exc.detail})
