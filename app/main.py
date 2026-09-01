from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import socketio
from fastapi import FastAPI

from app.accounts.router import router as accounts_router
from app.admin.companies_router import router as admin_companies_router
from app.admin.payments_router import router as admin_payments_router
from app.admin.router import router as admin_router
from app.conversations import socket_handlers  # noqa: F401  (registers Socket.IO event handlers)
from app.conversations.router import router as conversations_router
from app.conversations.socket_server import sio
from app.core.clients.asaas_client import close_asaas_client
from app.core.clients.butter_mind_client import close_butter_mind_client
from app.core.exceptions import register_exception_handlers
from app.invoices.router import account_router as account_invoices_router
from app.invoices.router import router as invoices_router
from app.knowledge.router import router as knowledge_router
from app.orders.router import router as orders_router
from app.payment_methods.router import router as payment_methods_router
from app.quotes.router import router as quotes_router
from app.service_catalog.router import router as service_catalog_router
from app.settings.config import settings
from app.settings.logging_config import setup_logging
from app.settings.middleware import register_middleware
from app.webhooks.router import admin_router as admin_webhooks_router
from app.webhooks.router import router as webhooks_router

setup_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await close_asaas_client()
    await close_butter_mind_client()


app = FastAPI(
    title="DevButter Platform",
    description=(
        "Orchestrator backend for the DevButter platform: accounts, AI-drafted "
        "and manual quotes, orders, and invoices. Holds no billing/chat logic of "
        "its own -- it calls asaas_payment, butter_mind, and butter_relay as clients."
    ),
    lifespan=lifespan,
)
register_middleware(app)
register_exception_handlers(app)

app.include_router(accounts_router)
app.include_router(conversations_router)
app.include_router(quotes_router)
app.include_router(orders_router)
app.include_router(payment_methods_router)
app.include_router(invoices_router)
app.include_router(account_invoices_router)
app.include_router(webhooks_router)
app.include_router(admin_webhooks_router)
app.include_router(service_catalog_router)
app.include_router(admin_router)
app.include_router(admin_payments_router)
app.include_router(admin_companies_router)
app.include_router(knowledge_router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ASGI app actually served at runtime: mounts the Socket.IO server (real-time
# chat transport, see docs/plano-streaming-socketio-chat.md) alongside the
# FastAPI `app` above. Tests and internal code should keep importing `app`
# directly (e.g. httpx.ASGITransport(app=app)) -- only the process entrypoint
# (Dockerfile CMD) needs `asgi_app`.
asgi_app = socketio.ASGIApp(sio, other_asgi_app=app)
