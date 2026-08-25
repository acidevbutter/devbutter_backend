import base64
from uuid import uuid4

import pytest

from app.core.secure_payload import (
    SecurePayloadError,
    decrypt_payload,
    encrypt_payload,
)


def test_round_trip_and_aad_binding() -> None:
    key = bytes(range(32))
    nonce = bytes(range(12))
    request_id = str(uuid4())
    payload = {"cardholder": "Teste", "month": 12, "year": 2030}

    envelope = encrypt_payload(
        payload,
        key=key,
        kid="info-web-test-01",
        context_id="secctx_test_context_0001",
        purpose="payment_method.create",
        method="post",
        path="/payment-methods",
        nonce=nonce,
        request_id=request_id,
        timestamp=1_700_000_000,
    )

    assert decrypt_payload(
        envelope,
        key=key,
        expected_context_id="secctx_test_context_0001",
        expected_nonce=nonce,
        expected_purpose="payment_method.create",
        expected_method="POST",
        expected_path="/payment-methods",
        now=1_700_000_100,
    ) == payload

    with pytest.raises(SecurePayloadError, match="invalid_secure_payload"):
        decrypt_payload(
            envelope,
            key=key,
            expected_context_id="secctx_test_context_0001",
            expected_nonce=nonce,
            expected_purpose="payment_method.create",
            expected_method="POST",
            expected_path="/other-path",
            now=1_700_000_100,
        )


def test_rejects_noncanonical_base64_and_duplicate_plaintext_keys() -> None:
    key = bytes(32)
    nonce = bytes(12)
    envelope = encrypt_payload(
        {"ok": True}, key=key, kid="info-web-test-01", context_id="secctx_test_context_0001",
        purpose="payment_method.create", method="POST", path="/payment-methods", nonce=nonce,
        request_id="00000000-0000-4000-8000-000000000001", timestamp=1_700_000_000,
    )
    envelope["tag"] = base64.urlsafe_b64encode(bytes(16)).decode()
    with pytest.raises(SecurePayloadError, match="invalid_secure_payload"):
        decrypt_payload(
            envelope, key=key, expected_context_id="secctx_test_context_0001", expected_nonce=nonce,
            expected_purpose="payment_method.create", expected_method="POST",
            expected_path="/payment-methods", now=1_700_000_000,
        )
