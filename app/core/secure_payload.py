"""Version 1 secure envelope shared by the browser and the backend.

This module encrypts transport payloads only. It is not authentication,
authorization, PCI scope reduction, or token-at-rest storage.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
import time
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_KID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
_PURPOSE_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")
_CONTEXT_RE = re.compile(r"^secctx_[A-Za-z0-9_-]+$")
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_MAX_CIPHERTEXT = 16 * 1024


class SecurePayloadError(ValueError):
    """Safe, non-sensitive error for malformed or unauthenticated payloads."""


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str, *, expected_length: int | None = None) -> bytes:
    if not value or "=" in value or not _B64URL_RE.fullmatch(value):
        raise SecurePayloadError("invalid_secure_payload")
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode(value + padding)
    except (ValueError, binascii.Error) as exc:
        raise SecurePayloadError("invalid_secure_payload") from exc
    if _b64url_encode(decoded) != value:
        raise SecurePayloadError("invalid_secure_payload")
    if expected_length is not None and len(decoded) != expected_length:
        raise SecurePayloadError("invalid_secure_payload")
    return decoded


def key_from_base64url(value: str) -> bytes:
    key = _b64url_decode(value, expected_length=32)
    return key


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SecurePayloadError("invalid_secure_payload")
        result[key] = value
    return result


def parse_json_object(value: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(value.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, SecurePayloadError) as exc:
        raise SecurePayloadError("invalid_secure_payload") from exc
    if not isinstance(parsed, dict):
        raise SecurePayloadError("invalid_secure_payload")
    return parsed


def _validate_json_value(value: Any, depth: int = 0) -> None:
    if depth > 8:
        raise SecurePayloadError("invalid_secure_payload")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SecurePayloadError("invalid_secure_payload")
        raise SecurePayloadError("invalid_secure_payload")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise SecurePayloadError("invalid_secure_payload")
            _validate_json_value(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth + 1)
    elif not isinstance(value, str | int | bool) and value is not None:
        raise SecurePayloadError("invalid_secure_payload")


def canonical_json_bytes(value: Any, *, ascii_only: bool = False) -> bytes:
    _validate_json_value(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SecurePayloadError("invalid_secure_payload") from exc
    if ascii_only and any(byte > 0x7F for byte in encoded):
        raise SecurePayloadError("invalid_secure_payload")
    return encoded


def build_aad(
    *,
    context_id: str,
    kid: str,
    method: str,
    path: str,
    purpose: str,
    request_id: str,
    timestamp: int,
) -> bytes:
    fields = {
        "contextId": context_id,
        "kid": kid,
        "method": method.upper(),
        "path": path,
        "purpose": purpose,
        "requestId": request_id,
        "ts": timestamp,
        "v": 1,
    }
    if "?" in path or not path.startswith("/"):
        raise SecurePayloadError("invalid_secure_payload")
    return canonical_json_bytes(fields, ascii_only=True)


def _validate_envelope(envelope: Mapping[str, Any]) -> None:
    required = {
        "v", "kid", "ts", "requestId", "contextId", "purpose", "nonce", "ciphertext", "tag"
    }
    if set(envelope) != required:
        raise SecurePayloadError("invalid_secure_payload")
    if (
        envelope["v"] != 1
        or not isinstance(envelope["ts"], int)
        or isinstance(envelope["ts"], bool)
    ):
        raise SecurePayloadError("invalid_secure_payload")
    if not 0 <= envelope["ts"] <= 253402300799:
        raise SecurePayloadError("invalid_secure_payload")
    if not isinstance(envelope["kid"], str) or not _KID_RE.fullmatch(envelope["kid"]):
        raise SecurePayloadError("invalid_secure_payload")
    if not isinstance(envelope["purpose"], str) or not _PURPOSE_RE.fullmatch(envelope["purpose"]):
        raise SecurePayloadError("invalid_secure_payload")
    if (
        not isinstance(envelope["contextId"], str)
        or not _CONTEXT_RE.fullmatch(envelope["contextId"])
    ):
        raise SecurePayloadError("invalid_secure_payload")
    if not isinstance(envelope["requestId"], str) or not _UUID4_RE.fullmatch(envelope["requestId"]):
        raise SecurePayloadError("invalid_secure_payload")
    for field in ("nonce", "ciphertext", "tag"):
        if not isinstance(envelope[field], str):
            raise SecurePayloadError("invalid_secure_payload")
    _b64url_decode(envelope["nonce"], expected_length=12)
    ciphertext = _b64url_decode(envelope["ciphertext"])
    _b64url_decode(envelope["tag"], expected_length=16)
    if not 1 <= len(ciphertext) <= _MAX_CIPHERTEXT:
        raise SecurePayloadError("invalid_secure_payload")


def encrypt_payload(
    payload: Mapping[str, Any],
    *,
    key: bytes,
    kid: str,
    context_id: str,
    purpose: str,
    method: str,
    path: str,
    nonce: bytes,
    request_id: str,
    timestamp: int,
) -> dict[str, Any]:
    if len(key) != 32 or len(nonce) != 12:
        raise SecurePayloadError("invalid_secure_payload")
    plaintext = canonical_json_bytes(payload)
    if len(plaintext) > _MAX_CIPHERTEXT:
        raise SecurePayloadError("invalid_secure_payload")
    aad = build_aad(
        context_id=context_id, kid=kid, method=method, path=path, purpose=purpose,
        request_id=request_id, timestamp=timestamp,
    )
    encrypted = AESGCM(key).encrypt(nonce, plaintext, aad)
    envelope = {
        "v": 1,
        "kid": kid,
        "ts": timestamp,
        "requestId": request_id,
        "contextId": context_id,
        "purpose": purpose,
        "nonce": _b64url_encode(nonce),
        "ciphertext": _b64url_encode(encrypted[:-16]),
        "tag": _b64url_encode(encrypted[-16:]),
    }
    _validate_envelope(envelope)
    return envelope


def decrypt_payload(
    envelope: Mapping[str, Any],
    *,
    key: bytes,
    expected_context_id: str,
    expected_nonce: bytes,
    expected_purpose: str,
    expected_method: str,
    expected_path: str,
    now: int | None = None,
    clock_skew_seconds: int = 300,
) -> dict[str, Any]:
    _validate_envelope(envelope)
    if len(key) != 32 or envelope["contextId"] != expected_context_id:
        raise SecurePayloadError("invalid_secure_payload")
    nonce = _b64url_decode(envelope["nonce"], expected_length=12)
    if nonce != expected_nonce or envelope["purpose"] != expected_purpose:
        raise SecurePayloadError("invalid_secure_payload")
    current = int(time.time()) if now is None else now
    if abs(current - envelope["ts"]) > clock_skew_seconds:
        raise SecurePayloadError("invalid_secure_payload")
    aad = build_aad(
        context_id=envelope["contextId"], kid=envelope["kid"], method=expected_method,
        path=expected_path, purpose=envelope["purpose"], request_id=envelope["requestId"],
        timestamp=envelope["ts"],
    )
    ciphertext = _b64url_decode(envelope["ciphertext"])
    tag = _b64url_decode(envelope["tag"], expected_length=16)
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext + tag, aad)
    except Exception as exc:  # cryptography intentionally exposes no tag detail
        raise SecurePayloadError("invalid_secure_payload") from exc
    if len(plaintext) > _MAX_CIPHERTEXT:
        raise SecurePayloadError("invalid_secure_payload")
    return parse_json_object(plaintext)


def validate_request_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise SecurePayloadError("invalid_secure_payload") from exc
    if parsed.version != 4 or str(parsed) != value.lower():
        raise SecurePayloadError("invalid_secure_payload")
    return value
