"""Cross-runtime interop helper for F06-05 (payment-tokenization security review).

NOT part of the application; only invoked as a subprocess by the frontend
interop test (devbutter_frontend/src/lib/interop/secure-payload.interop.test.ts).

It imports the real, unmodified app.core.secure_payload module and drives its
real encrypt_payload() -- this is a thin I/O adapter, not a reimplementation
of any crypto or AAD logic. Whatever envelope comes out of this script is
byte-for-byte what the backend would produce, because it *is* the backend's
encrypt_payload().

Protocol: read one JSON object from stdin, write one JSON envelope to stdout,
exit 0/1 (errors go to stderr).

Input shape:
{
  "keyB64": str,      # base64url, 32 bytes, no padding
  "kid": str,
  "contextId": str,
  "purpose": str,
  "method": str,
  "path": str,
  "nonceB64": str,    # base64url, 12 bytes, no padding
  "requestId": str,
  "timestamp": int,   # unix seconds
  "payload": dict      # plaintext to encrypt
}

Run manually:
  echo '{"keyB64": "...", ...}' | poetry run python scripts/interop/secure_payload_encrypt_cli.py
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.secure_payload import encrypt_payload, key_from_base64url  # noqa: E402


def _b64url_to_bytes(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def main() -> None:
    request = json.loads(sys.stdin.read())

    key = key_from_base64url(request["keyB64"])
    nonce = _b64url_to_bytes(request["nonceB64"])

    envelope = encrypt_payload(
        request["payload"],
        key=key,
        kid=request["kid"],
        context_id=request["contextId"],
        purpose=request["purpose"],
        method=request["method"],
        path=request["path"],
        nonce=nonce,
        request_id=request["requestId"],
        timestamp=request["timestamp"],
    )
    sys.stdout.write(json.dumps(envelope))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 -- CLI boundary, report and exit non-zero
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        sys.exit(1)
