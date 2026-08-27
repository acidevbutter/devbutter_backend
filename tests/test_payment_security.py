from uuid import uuid4

import pytest

from app.core.secure_payload import SecurePayloadError, encrypt_payload
from app.payment_security.service import SecurePaymentContext, SecurePaymentPayloadService


def _context(nonce: bytes) -> SecurePaymentContext:
    return SecurePaymentContext(
        context_id="secctx_test_context_0001",
        nonce=nonce,
        purpose="payment_method.create",
        method="POST",
        path="/payment-methods",
        now=1_700_000_000,
        tenant_id="company-1",
        subject_id="account-1",
        invoice_id=None,
        amount_minor=None,
        currency=None,
    )


def test_decrypts_with_server_keyring_and_returns_safe_metadata() -> None:
    key = bytes(range(32))
    nonce = bytes(range(12))
    envelope = encrypt_payload(
        {"cardNumber": "never-log-this", "cvv": "never-log-this"},
        key=key,
        kid="info-web-test-01",
        context_id="secctx_test_context_0001",
        purpose="payment_method.create",
        method="POST",
        path="/payment-methods",
        nonce=nonce,
        request_id=str(uuid4()),
        timestamp=1_700_000_000,
    )

    service = SecurePaymentPayloadService({"info-web-test-01": key})
    decrypted = service.decrypt_for_processing(envelope, context=_context(nonce))
    assert decrypted["cvv"] == "never-log-this"
    metadata = service.safe_metadata(envelope)
    assert "ciphertext" not in metadata
    assert "cardNumber" not in metadata


def test_unknown_kid_is_rejected_without_fallback_key() -> None:
    key = bytes(32)
    nonce = bytes(12)
    envelope = encrypt_payload(
        {"ok": True},
        key=key,
        kid="info-web-test-01",
        context_id="secctx_test_context_0001",
        purpose="payment_method.create",
        method="POST",
        path="/payment-methods",
        nonce=nonce,
        request_id=str(uuid4()),
        timestamp=1_700_000_000,
    )

    with pytest.raises(SecurePayloadError, match="invalid_secure_payload"):
        SecurePaymentPayloadService({}).decrypt_for_processing(envelope, context=_context(nonce))
