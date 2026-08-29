"""Reactive endpoint that selolibertaria/backend calls when it gets a 401
from the Asaas API with its current credential --
docs/prompts/asaas-payment-integration/03-prompt-devbutter-relay-e-rotacao.md,
entregavel 4. Authenticated with the same HMAC scheme as the outbound relay
(CONTRATO-HMAC.md), but a *dedicated* shared secret
(DEVBUTTER_SELOLIBERTARIA_RELAY_HMAC_SECRET) -- not TRUSTED_PROXY_SECRET or
AUDIT_SIGNING_KEY (asaas_payment's own mTLS trust boundary / audit-row
signing, unrelated purpose).
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, status

from app.accounts.repository import AsaasSubaccountLinkRepository
from app.core.clients.asaas_client import AsaasClient, get_asaas_client
from app.core.dependencies import DbSession
from app.core.exceptions import ConflictError, NotFoundError, UnauthorizedError
from app.integrations.hmac_signing import verify
from app.integrations.relay_service import SelolibertariaRelayService
from app.integrations.rotation_service import CredentialRotationService
from app.integrations.selolibertaria_client import SelolibertariaClient, get_selolibertaria_client
from app.settings.config import settings
from app.webhooks.repository import WebhookEventRepository

router = APIRouter(prefix="/integrations/selolibertaria", tags=["integrations"])


def get_rotation_service(
    db: DbSession,
    asaas_client: Annotated[AsaasClient, Depends(get_asaas_client)],
    relay_client: Annotated[SelolibertariaClient, Depends(get_selolibertaria_client)],
) -> CredentialRotationService:
    link_repository = AsaasSubaccountLinkRepository(db)
    relay_service = SelolibertariaRelayService(relay_client, link_repository)
    return CredentialRotationService(asaas_client, link_repository, relay_service)


RotationServiceDep = Annotated[CredentialRotationService, Depends(get_rotation_service)]


@router.post(
    "/subaccounts/{asaas_account_id}/rotate-key",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reactive key rotation: Selolibertaria detected a 401 from Asaas",
)
async def rotate_key(
    asaas_account_id: str,
    request: Request,
    db: DbSession,
    rotation_service: RotationServiceDep,
    x_signature: Annotated[str | None, Header(alias="X-Selolibertaria-Signature")] = None,
    x_timestamp: Annotated[str | None, Header(alias="X-Selolibertaria-Timestamp")] = None,
    x_nonce: Annotated[str | None, Header(alias="X-Selolibertaria-Nonce")] = None,
) -> dict[str, Any]:
    raw_body = await request.body()
    if not verify(
        settings.devbutter_selolibertaria_relay_hmac_secret,
        timestamp=x_timestamp,
        nonce=x_nonce,
        signature=x_signature,
        raw_body=raw_body,
    ):
        # Body per CONTRATO-HMAC.md's "Body de erro em assinatura invalida" --
        # never echo the received signature or the secret.
        raise UnauthorizedError("invalid_signature")

    try:
        body = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    # event_id is generated locally when the caller omits it, so every
    # call still leaves exactly one audit row in webhook_events -- see
    # docs/auditoria-seguranca-2026-08-29.md item 2. A locally-generated
    # event_id never collides with a real dedupe key, so this only affects
    # auditability, not idempotency, for callers that omit event_id.
    event_id = body.get("event_id") or str(uuid.uuid4())
    reason = body.get("reason", "unauthorized")

    event_repository = WebhookEventRepository(db)
    existing = await event_repository.get_by_event_id(event_id)
    if existing is not None:
        # Same event_id reprocessed is a no-op per CONTRATO-HMAC.md.
        return {"received": True}

    link_repository = AsaasSubaccountLinkRepository(db)
    link = await link_repository.get_by_asaas_account_id(asaas_account_id)
    if link is None:
        raise NotFoundError("Subaccount not found")

    try:
        await event_repository.create(
            event_type="selolibertaria.rotate_key_request",
            related_invoice_id=None,
            payload={"event_id": event_id, "asaas_account_id": asaas_account_id, "reason": reason},
            event_id=event_id,
        )
    except ConflictError:
        # Lost the race: a concurrent request with the same event_id (the
        # get_by_event_id check above raced with another request's create)
        # already committed its audit row first. Same observable effect as
        # the check above finding it -- no-op, do not rotate again.
        return {"received": True}

    await rotation_service.rotate(link, reason="reactive")
    return {"received": True}
