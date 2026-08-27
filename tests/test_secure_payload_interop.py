"""Cross-runtime interop test for F06-05 (payment-tokenization security review).

Closes: "interoperabilidade não foi comprovada por vetor TS -> Python" --
until now, devbutter_frontend/src/lib/secure-payload.ts and
devbutter_backend/app/core/secure_payload.py each had same-language
round-trip tests plus a typecheck, but nothing proved the two independent
implementations actually agree on wire format, canonical JSON, AAD
construction, or AES-256-GCM output.

This test spawns the REAL TypeScript encryptPayload() as a subprocess (via
devbutter_frontend/scripts/interop/secure-payload-encrypt-cli.mts, a thin
stdin/stdout adapter around the unmodified secure-payload.ts -- no crypto or
AAD logic is reimplemented there), feeds the exact resulting envelope into
this repo's real decrypt_payload(), and asserts the plaintext round-trips.
Negative vectors mutate the TS-produced envelope and confirm this repo's
real decrypt_payload() rejects it, still using the real implementations end
to end -- never a hand-copied literal standing in for either side.

The complementary direction (Python encrypts, TypeScript decrypts) lives in
devbutter_frontend/src/lib/interop/secure-payload.interop.test.ts and runs
under `yarn test`.

Run:
    poetry run pytest tests/test_secure_payload_interop.py -v
(also collected automatically by `poetry run pytest`.)

Requires `node` on PATH and the sibling devbutter_frontend checkout next to
this repo; skipped with a clear reason if either is unavailable so the rest
of the backend suite is unaffected by frontend toolchain availability.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.secure_payload import SecurePayloadError, decrypt_payload

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "devbutter_frontend"
NODE_CLI = FRONTEND_DIR / "scripts" / "interop" / "secure-payload-encrypt-cli.mts"
NODE_BIN = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE_BIN is None or not NODE_CLI.exists(),
    reason=(
        "cross-runtime interop test requires `node` on PATH and the sibling "
        "devbutter_frontend checkout with scripts/interop/secure-payload-encrypt-cli.mts"
    ),
)

# Fixed, fictional, 32-byte key -- base64url(bytes(range(32))). Never a real
# secret; deliberately deterministic so this vector is reproducible and
# reviewable. Real key material is never generated this way (see contract
# docs/prompts/payment-tokenization/runs/02-crypto-contract.md sec 1).
TEST_KEY_B64 = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
TEST_KEY = bytes(range(32))
TEST_NONCE_B64 = "AAECAwQFBgcICQoL"
TEST_NONCE = bytes(range(12))


def _ts_encrypt(**request: object) -> dict:
    """Invoke the REAL TypeScript encryptPayload() via subprocess and return its envelope."""

    proc = subprocess.run(
        [NODE_BIN, str(NODE_CLI)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"TypeScript encrypt CLI failed (exit {proc.returncode}): {proc.stderr}")
    return json.loads(proc.stdout)


def _base_request(**overrides: object) -> dict:
    base: dict[str, object] = dict(
        keyB64=TEST_KEY_B64,
        kid="interop-test-key-0001",
        contextId="secctx_interop_test_0001",
        purpose="payment_method.create",
        method="POST",
        path="/payment-methods",
        nonceB64=TEST_NONCE_B64,
        requestId=str(uuid4()),
        timestamp=1_700_000_000,
        payload={"cardholder": "Interop Test", "month": 12, "year": 2031},
    )
    base.update(overrides)
    return base


def _tamper_b64url(value: str) -> str:
    """Flip one bit in the decoded bytes and re-encode. Mutating a trailing
    base64 character directly can land on padding bits that carry no
    decoded data (a silent non-tamper); decode/flip/re-encode guarantees an
    actual ciphertext/tag mutation.
    """

    raw = bytearray(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    raw[0] ^= 0xFF
    return base64.urlsafe_b64encode(bytes(raw)).decode("ascii").rstrip("=")


def _decrypt(envelope: dict, req: dict, **overrides: object) -> dict:
    kwargs = dict(
        key=TEST_KEY,
        expected_context_id=req["contextId"],
        expected_nonce=TEST_NONCE,
        expected_purpose=req["purpose"],
        expected_method=req["method"],
        expected_path=req["path"],
        now=req["timestamp"],
    )
    kwargs.update(overrides)
    return decrypt_payload(envelope, **kwargs)


def test_ts_encrypts_python_decrypts_round_trip() -> None:
    """The core interop proof: a real TS envelope decrypts cleanly in Python."""

    req = _base_request()
    envelope = _ts_encrypt(**req)
    plaintext = _decrypt(envelope, req)

    assert plaintext == req["payload"]


def test_ts_encrypts_matches_frozen_contract_vector() -> None:
    """The TS implementation reproduces the vector in
    docs/prompts/payment-tokenization/runs/02-crypto-contract.md section 12
    exactly -- confirming the deterministic positive vector is not just
    Python-only, as the finding alleged.
    """

    envelope = _ts_encrypt(
        keyB64="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        kid="test-key-0001",
        contextId="secctx_test_0001",
        purpose="payment_method.create",
        method="POST",
        path="/payment-methods",
        nonceB64="AAAAAAAAAAAAAAAA",
        requestId="00000000-0000-4000-8000-000000000001",
        timestamp=1_700_000_000,
        payload={"sample": "fictional-card-payload"},
    )

    assert envelope["ciphertext"] == "tYUzXCAQBwsldOe105DpcR0OYqYaxUsGtY-F7wxqWu-5aMw"
    assert envelope["tag"] == "OXsza2XKq4T6_XTh3c0KxA"

    plaintext = decrypt_payload(
        envelope,
        key=bytes(32),
        expected_context_id="secctx_test_0001",
        expected_nonce=bytes(12),
        expected_purpose="payment_method.create",
        expected_method="POST",
        expected_path="/payment-methods",
        now=1_700_000_000,
    )
    assert plaintext == {"sample": "fictional-card-payload"}


def test_ts_encrypted_tampered_tag_rejected_by_python() -> None:
    req = _base_request()
    envelope = _ts_encrypt(**req)
    envelope["tag"] = _tamper_b64url(envelope["tag"])

    with pytest.raises(SecurePayloadError, match="invalid_secure_payload"):
        _decrypt(envelope, req)


def test_ts_encrypted_tampered_ciphertext_rejected_by_python() -> None:
    req = _base_request()
    envelope = _ts_encrypt(**req)
    envelope["ciphertext"] = _tamper_b64url(envelope["ciphertext"])

    with pytest.raises(SecurePayloadError, match="invalid_secure_payload"):
        _decrypt(envelope, req)


def test_ts_encrypted_wrong_aad_path_rejected_by_python() -> None:
    """AAD binds `path` (contract sec 5); a route mismatch must fail authentication."""

    req = _base_request()
    envelope = _ts_encrypt(**req)

    with pytest.raises(SecurePayloadError, match="invalid_secure_payload"):
        _decrypt(envelope, req, expected_path="/payments")


def test_ts_encrypted_timestamp_outside_skew_rejected_by_python() -> None:
    """`ts` clock-skew (+/-300s) is checked inside decrypt_payload itself
    (contract sec 7) -- this is genuinely envelope-level, unlike full replay
    protection which lives in the anti-replay ledger
    (app/payment_security/service.py + the secure_payment_context table
    from docs/prompts/payment-tokenization/runs/07-contract-freeze.md sec 4)
    and is out of scope for this envelope-only interop test.
    """

    req = _base_request()
    envelope = _ts_encrypt(**req)

    with pytest.raises(SecurePayloadError, match="invalid_secure_payload"):
        _decrypt(envelope, req, now=req["timestamp"] + 301)
