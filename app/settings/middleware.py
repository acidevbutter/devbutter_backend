import logging
import time
import uuid
from collections import defaultdict, deque

from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.metrics import emit_metrics, route_template, status_class
from app.settings.config import settings

logger = logging.getLogger("app.request")


class ConversationRateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window per-IP cap on the write endpoints of the anonymous
    concierge funnel (ADR-0004). In-memory / per-process -- enough to stop a
    single client turning the funnel into an open LLM tap; not a
    distributed limiter. `conversation_rate_limit_per_minute = 0` disables it.
    """

    _WINDOW_SECONDS = 60.0
    _GUARDED_PREFIXES = ("/conversations",)
    _GUARDED_SUFFIXES = ("", "/messages", "/select-option", "/business-profile")

    def __init__(self, app) -> None:
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

    async def dispatch(self, request: Request, call_next):
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
    """Assigns a request id, times the request, and logs one line per request."""

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()
        response = None
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request %s failed: %s %s", request_id, request.method, request.url.path
            )
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            status_code = response.status_code if response is not None else 500
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
        logger.info(
            "%s %s -> %s (%.1fms) [%s]",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )
        return response


def register_middleware(app) -> None:
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(ConversationRateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
