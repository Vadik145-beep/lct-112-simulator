"""Password hashing (argon2) and JWT access / refresh tokens."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

from app.config import get_settings

_hasher = PasswordHasher()

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def _now() -> datetime:
    return datetime.now(UTC)


def create_token(user_id: uuid.UUID, role: str, token_version: int, kind: str) -> str:
    settings = get_settings()
    if kind == "access":
        ttl = timedelta(minutes=settings.access_token_minutes)
    elif kind == "refresh":
        ttl = timedelta(days=settings.refresh_token_days)
    else:
        raise ValueError(kind)
    payload = {
        "sub": str(user_id),
        "role": role,
        "tv": token_version,
        "type": kind,
        "iat": _now(),
        "exp": _now() + ttl,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str, kind: str) -> dict | None:
    """Returns the payload or None if the token is invalid, expired or of the wrong kind."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != kind:
        return None
    return payload
