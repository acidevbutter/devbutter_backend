from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.core.secure_payload import SecurePayloadError, decrypt_payload


@dataclass(frozen=True, slots=True)
class SecurePaymentContext:
    """Server-derived request context; never accept these values from trust alone.

    `tenant_id`, `subject_id`, `invoice_id`, `amount_minor` and `currency`
    (contract-freeze section 2.2) are ownership/authorization facts read
    from the anti-replay ledger row -- resolved exclusively from server-side
    state (session + database), never from the envelope's plaintext or from
    any client-supplied field. They are not part of the AEAD's Additional
    Authenticated Data (that stays exactly the 8 fields fixed by the crypto
    contract) and `decrypt_for_processing` below does not read them; they
    exist so the caller has one place to carry ownership context alongside
    the crypto context through the request lifecycle.
    """

    context_id: str
    nonce: bytes
    purpose: str
    method: str
    path: str
    now: int
    tenant_id: str
    subject_id: str
    invoice_id: str | None
    amount_minor: int | None
    currency: str | None


class SecurePaymentPayloadService:
    """Decrypt transport envelopes without persisting or logging plaintext.

    The caller must obtain the context and keyring from server-side state. This
    class deliberately has no Asaas or database dependency until replay,
    ownership and token-at-rest contracts are closed.
    """

    def __init__(self, keyring: Mapping[str, bytes]):
        self._keyring = dict(keyring)

    def decrypt_for_processing(
        self,
        envelope: Mapping[str, Any],
        *,
        context: SecurePaymentContext,
    ) -> dict[str, Any]:
        kid = envelope.get("kid")
        if not isinstance(kid, str):
            raise SecurePayloadError("invalid_secure_payload")
        key = self._keyring.get(kid)
        if key is None:
            raise SecurePayloadError("invalid_secure_payload")
        return decrypt_payload(
            envelope,
            key=key,
            expected_context_id=context.context_id,
            expected_nonce=context.nonce,
            expected_purpose=context.purpose,
            expected_method=context.method,
            expected_path=context.path,
            now=context.now,
        )

    @staticmethod
    def safe_metadata(envelope: Mapping[str, Any]) -> dict[str, Any]:
        """Return only non-secret correlation fields suitable for logs."""

        return {
            "v": envelope.get("v"),
            "kid": envelope.get("kid"),
            "ts": envelope.get("ts"),
            "requestId": envelope.get("requestId"),
            "contextId": envelope.get("contextId"),
            "purpose": envelope.get("purpose"),
        }
