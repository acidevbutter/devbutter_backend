from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.core.secure_payload import SecurePayloadError, decrypt_payload


@dataclass(frozen=True, slots=True)
class SecurePaymentContext:
    """Server-derived request context; never accept these values from trust alone."""

    context_id: str
    nonce: bytes
    purpose: str
    method: str
    path: str
    now: int


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
