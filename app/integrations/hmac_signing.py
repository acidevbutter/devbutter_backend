"""HMAC signature scheme shared with selolibertaria/backend for the
credential relay + rotation server-to-server routes -- implements
docs/prompts/asaas-payment-integration/CONTRATO-HMAC.md exactly. Both repos
implement this module identically; do not change the algorithm here without
updating the contract and the other repo in lockstep.

Algorithm: HMAC-SHA256, hex digest, over
`f"{timestamp}.{nonce}.{raw_body_bytes.decode('utf-8')}"` -- the exact raw
JSON body bytes sent on the wire, not a re-serialization. `timestamp` is
epoch seconds as a string; a request is rejected if it is more than 5
minutes (300s) away from wall-clock time. `nonce` is an opaque per-request
string (uuid4 hex here) -- defense in depth against replay within that
window; business-level idempotency (dedupe by event_id) is what actually
guarantees no duplicated effect, not the nonce.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass

MAX_CLOCK_SKEW_SECONDS = 300


@dataclass(frozen=True)
class SignedRequest:
    signature: str
    timestamp: str
    nonce: str


def _digest(secret: str, timestamp: str, nonce: str, raw_body: bytes) -> str:
    message = f"{timestamp}.{nonce}.{raw_body.decode('utf-8')}".encode()
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def sign(secret: str, raw_body: bytes) -> SignedRequest:
    """Builds a fresh timestamp + nonce + signature for one outbound
    request. Call this again for every retry attempt of the same logical
    delivery -- the timestamp must stay within the receiver's 5-minute
    window even if earlier attempts are still in flight/backing off.
    """
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    signature = _digest(secret, timestamp, nonce, raw_body)
    return SignedRequest(signature=signature, timestamp=timestamp, nonce=nonce)


def verify(
    secret: str,
    *,
    timestamp: str | None,
    nonce: str | None,
    signature: str | None,
    raw_body: bytes,
) -> bool:
    """Constant-time verification per the contract -- always
    hmac.compare_digest, never `==`/`!=`. Returns False (never raises) for
    every failure mode -- missing/malformed headers, stale timestamp, or a
    mismatched signature -- so the caller can respond uniformly with the
    contract's `401 {"detail": "invalid_signature"}` without distinguishing
    which check failed.
    """
    if not secret or not timestamp or not nonce or not signature:
        return False
    try:
        timestamp_value = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - timestamp_value) > MAX_CLOCK_SKEW_SECONDS:
        return False
    expected = _digest(secret, timestamp, nonce, raw_body)
    return hmac.compare_digest(expected, signature)
