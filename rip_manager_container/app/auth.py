"""Four-digit PIN hashing, sessions and brute-force throttling.

The PIN is never stored or compared in the browser. It is PBKDF2-hashed here,
and because ten thousand combinations is a small keyspace, repeated failures
from one client are slowed down before the lock becomes decorative.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from typing import Dict, Optional, Tuple

from fastapi import Response

from config import (
    LOGIN_LOCKOUT_SECONDS,
    LOGIN_MAX_ATTEMPTS,
    SESSION_COOKIE,
    SESSION_COOKIE_SECURE,
    SESSION_SECONDS,
)
import db

PBKDF2_ROUNDS = 310_000

_attempts: Dict[str, Tuple[int, float]] = {}
_attempts_lock = threading.Lock()


def pin_is_set() -> bool:
    return bool(db.get_setting("pin_hash"))


def hash_pin(pin: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_pin(pin: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", pin.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
        ).hex()
        return hmac.compare_digest(actual, digest_hex)
    except (AttributeError, TypeError, ValueError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_valid(token: Optional[str]) -> bool:
    if not token:
        return False
    row = db.query_one(
        "SELECT expires_at FROM auth_sessions WHERE token_hash=?", (_token_hash(token),)
    )
    return bool(row and row["expires_at"] > time.time())


def issue_session(response: Response) -> None:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with db.write() as conn:
        conn.execute(
            "INSERT INTO auth_sessions(token_hash,created_at,expires_at) VALUES (?,?,?)",
            (_token_hash(token), now, now + SESSION_SECONDS),
        )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="strict",
        secure=SESSION_COOKIE_SECURE,
        path="/",
    )


def revoke_session(token: Optional[str], response: Response) -> None:
    if token:
        with db.write() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (_token_hash(token),))
    response.delete_cookie(SESSION_COOKIE, path="/")


def revoke_all_sessions() -> None:
    with db.write() as conn:
        conn.execute("DELETE FROM auth_sessions")


def throttle_remaining(client: str) -> float:
    """Seconds this client must wait, or zero when it may try again."""
    with _attempts_lock:
        count, last = _attempts.get(client, (0, 0.0))
    if count < LOGIN_MAX_ATTEMPTS:
        return 0.0
    # Each failure past the threshold doubles the wait, capped at ten minutes.
    penalty = min(LOGIN_LOCKOUT_SECONDS * (2 ** (count - LOGIN_MAX_ATTEMPTS)), 600.0)
    return max(0.0, (last + penalty) - time.time())


def record_failure(client: str) -> None:
    with _attempts_lock:
        count, _ = _attempts.get(client, (0, 0.0))
        _attempts[client] = (count + 1, time.time())


def clear_failures(client: str) -> None:
    with _attempts_lock:
        _attempts.pop(client, None)
