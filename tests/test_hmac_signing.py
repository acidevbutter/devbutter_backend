from __future__ import annotations

import time

from app.integrations.hmac_signing import sign, verify

SECRET = "test-relay-secret"


def test_sign_then_verify_round_trips():
    body = b'{"event_id":"abc","api_key":"should-never-be-logged"}'

    signed = sign(SECRET, body)

    assert verify(
        SECRET,
        timestamp=signed.timestamp,
        nonce=signed.nonce,
        signature=signed.signature,
        raw_body=body,
    )


def test_verify_rejects_tampered_body():
    body = b'{"amount": 10}'
    signed = sign(SECRET, body)

    assert not verify(
        SECRET,
        timestamp=signed.timestamp,
        nonce=signed.nonce,
        signature=signed.signature,
        raw_body=b'{"amount": 10000}',
    )


def test_verify_rejects_wrong_secret():
    body = b"{}"
    signed = sign(SECRET, body)

    assert not verify(
        "a-different-secret",
        timestamp=signed.timestamp,
        nonce=signed.nonce,
        signature=signed.signature,
        raw_body=body,
    )


def test_verify_rejects_stale_timestamp():
    body = b"{}"
    signed = sign(SECRET, body)
    stale_timestamp = str(int(float(signed.timestamp)) - 301)

    assert not verify(
        SECRET,
        timestamp=stale_timestamp,
        nonce=signed.nonce,
        signature=signed.signature,
        raw_body=body,
    )


def test_verify_rejects_missing_headers():
    body = b"{}"
    signed = sign(SECRET, body)

    assert not verify(
        SECRET, timestamp=None, nonce=signed.nonce, signature=signed.signature, raw_body=body
    )
    assert not verify(
        SECRET, timestamp=signed.timestamp, nonce=None, signature=signed.signature, raw_body=body
    )
    assert not verify(
        SECRET, timestamp=signed.timestamp, nonce=signed.nonce, signature=None, raw_body=body
    )


def test_verify_rejects_when_secret_not_configured():
    body = b"{}"
    signed = sign(SECRET, body)

    assert not verify(
        "",
        timestamp=signed.timestamp,
        nonce=signed.nonce,
        signature=signed.signature,
        raw_body=body,
    )


def test_sign_uses_current_time_and_a_fresh_nonce_per_call():
    body = b"{}"
    first = sign(SECRET, body)
    second = sign(SECRET, body)

    assert first.nonce != second.nonce
    assert abs(int(first.timestamp) - int(time.time())) < 5
