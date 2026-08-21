import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import bcrypt
import jwt

from app.settings.config import settings


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def _create_token(account_id: uuid.UUID, token_type: TokenType, expires_delta: timedelta) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(account_id),
        "type": token_type.value,
        "iat": now,
        "exp": now + expires_delta,
        # Ensures two tokens issued in the same second still differ -- also a
        # ready hook for a future revocation list, if that's ever needed.
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(account_id: uuid.UUID) -> str:
    return _create_token(
        account_id, TokenType.ACCESS, timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )


def create_refresh_token(account_id: uuid.UUID) -> str:
    return _create_token(
        account_id, TokenType.REFRESH, timedelta(days=settings.jwt_refresh_token_expire_days)
    )


class InvalidTokenError(Exception):
    pass


def decode_token(token: str, expected_type: TokenType) -> uuid.UUID:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("Token is invalid or expired") from exc
    if payload.get("type") != expected_type.value:
        raise InvalidTokenError(f"Expected a {expected_type.value} token")
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise InvalidTokenError("Token subject is malformed") from exc
