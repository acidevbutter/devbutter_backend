import logging
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.metrics import SERVICE_NAME, emit_metrics, route_template, status_class
from app.settings.config import settings

logger = logging.getLogger("app.request")
_PROBE_PATHS = frozenset({"/health", "/health/ready"})


def _logged_route(request: Request) -> str:
    """Route template for the completion log, e.g. "/accounts/{account_id}".

    Unlike `route_template` (used for metric dimensions, where an unmatched
    request must collapse to the fixed low-cardinality "<unmatched>" bucket),
    the log line falls back to the raw request path so a 404 is still
    diagnosable from `route` alone.
    """
    route = route_template(request)
    return request.url.path if route == "<unmatched>" else route


class ConversationRateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window per-IP cap on the write endpoints of the anonymous
    concierge funnel (ADR-0004). In-memory / per-process -- enough to stop a
    single client turning the funnel into an open LLM tap; not a
    distributed limiter. `conversation_rate_limit_per_minute = 0` disables it.
    """

    _WINDOW_SECONDS = 60.0
    _GUARDED_PREFIXES = ("/conversations",)
    _GUARDED_SUFFIXES = ("", "/messages", "/select-option", "/business-profile")

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _is_guarded(self, request: Request) -> bool:
        if request.method not in ("POST", "PATCH"):
            return False
        path = request.url.path.rstrip("/")
        if not any(path.startswith(p) for p in self._GUARDED_PREFIXES):
            return False
        tail = path[len("/conversations") :]
        # "" (start) or "/{id}<suffix>"
        return tail == "" or any(
            tail.endswith(s) for s in self._GUARDED_SUFFIXES if s
        )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        limit = settings.conversation_rate_limit_per_minute
        if limit > 0 and self._is_guarded(request):
            client_ip = request.client.host if request.client else "unknown"
            now = time.monotonic()
            hits = self._hits[client_ip]
            while hits and now - hits[0] > self._WINDOW_SECONDS:
                hits.popleft()
            if len(hits) >= limit:
                retry_after = int(self._WINDOW_SECONDS - (now - hits[0])) + 1
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Muitas requisições. Tente de novo em instantes."},
                    headers={"Retry-After": str(retry_after)},
                )
            hits.append(now)
        return await call_next(request)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, times the request, and emits exactly one
    structured JSON completion event per request (success or failure) --
    see app.settings.logging_config.JsonFormatter for the wire format.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()
        response = None
        base_log_fields = {
            "service": SERVICE_NAME,
            "environment": settings.environment,
            "request_id": request_id,
            "method": request.method,
        }
        try:
            response = await call_next(request)
        except Exception:
            # Routing (request.scope["route"]) happens *inside* call_next, so
            # the route template is only resolvable after it returns/raises
            # -- resolving it up front would always yield the raw path.
            duration_ms = (time.perf_counter() - start) * 1000
            # exc_info is picked up automatically by logger.exception(); never
            # pass the exception's str()/args here -- upstream exceptions in
            # this service (auth, payments) may carry request payloads.
            logger.exception(
                "request failed",
                extra={
                    **base_log_fields,
                    "route": _logged_route(request),
                    "event": "http_request_failed",
                    "status_code": 500,
                    "duration_ms": round(duration_ms, 1),
                },
            )
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            status_code = response.status_code if response is not None else 500
            if request.url.path not in _PROBE_PATHS:
                metric_values = [
                    ("RequestCount", 1, "Count"),
                    ("Latency", duration_ms, "Milliseconds"),
                ]
                if status_code >= 500:
                    metric_values.append(("5xxCount", 1, "Count"))
                elif status_code >= 400:
                    metric_values.append(("4xxCount", 1, "Count"))
                await emit_metrics(
                    {
                        "Method": request.method,
                        "Route": route_template(request),
                        "StatusClass": status_class(status_code),
                    },
                    metric_values,
                )
        assert response is not None
        response.headers["X-Request-ID"] = request_id
        if request.url.path in _PROBE_PATHS:
            return response
        logger.info(
            "request completed",
            extra={
                **base_log_fields,
                "route": _logged_route(request),
                "event": "http_request_completed",
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 1),
            },
        )
        return response


def register_middleware(app: FastAPI) -> None:
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(ConversationRateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
