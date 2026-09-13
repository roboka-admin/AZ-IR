"""Credentials, sessions and CSRF for the internal editorial panel (ADR-0010).

The public API stays read-only and unauthenticated; everything in this module serves the small
research team that *writes* data. The rules it lives by:

* Passwords are hashed with argon2id and never logged, never returned by any endpoint.
* A session cookie carries an opaque random token; storage keeps only its SHA-256 digest, so a
  leaked database does not hand out live sessions.
* Every comparison that guards a secret is constant time.
* Login is throttled per identity+address. A five-person team does not need fifty attempts a
  minute; an attacker does.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import OrderedDict

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

#: argon2id at the OWASP-recommended floor. Hashing is deliberately not free: it runs once per
#: login and once per account creation, never on a read path.
_PRODUCTION = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

#: The fixtures driver re-hashes its demo identities every time it is constructed (tests build
#: several). Same algorithm, light parameters, dev driver only -- production never sees it.
_FAST = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)


def _hasher(fast: bool) -> PasswordHasher:
    return _FAST if fast else _PRODUCTION


def hash_password(password: str, *, fast: bool = False) -> str:
    if not password:
        raise ValueError("refusing to hash an empty password")
    return _hasher(fast).hash(password)


def verify_password(password_hash: str | None, password: str, *, fast: bool = False) -> bool:
    """True only for a matching password. A malformed or empty hash is a miss, never an exception."""
    if not password_hash or not password:
        return False
    try:
        return _hasher(fast).verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str, *, fast: bool = False) -> bool:
    """Parameters drift upwards over the years; rehash on the next successful login."""
    try:
        return _hasher(fast).check_needs_rehash(password_hash)
    except (InvalidHashError, VerificationError):  # pragma: no cover - defensive
        return True


def new_token(nbytes: int = 32) -> str:
    """URL-safe opaque secret (session ids, CSRF tokens)."""
    return secrets.token_urlsafe(nbytes)


def token_digest(token: str) -> str:
    """What storage keeps instead of the token itself."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class LoginThrottle:
    """Bounded, process-local rate limit for credential checks.

    Not a substitute for a real WAF; it is the cheap defence that stops one script from trying the
    whole dictionary against one account. State is capped so the map cannot grow without limit.
    """

    def __init__(self, max_attempts: int = 5, window_seconds: float = 300.0, capacity: int = 4096) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._capacity = capacity
        self._events: OrderedDict[str, list[float]] = OrderedDict()

    def _prune(self, key: str, now: float) -> list[float]:
        events = [at for at in self._events.get(key, ()) if now - at < self.window_seconds]
        if events:
            self._events[key] = events
            self._events.move_to_end(key)
        else:
            self._events.pop(key, None)
        while len(self._events) > self._capacity:
            self._events.popitem(last=False)
        return events

    def wait_seconds(self, key: str) -> float:
        """How long this key must wait before the next attempt (0 means "go ahead")."""
        now = time.monotonic()
        events = self._prune(key, now)
        if len(events) < self.max_attempts:
            return 0.0
        return max(0.0, self.window_seconds - (now - events[0]))

    def register_failure(self, key: str) -> None:
        now = time.monotonic()
        events = self._prune(key, now)
        events.append(now)
        self._events[key] = events

    def register_success(self, key: str) -> None:
        self._events.pop(key, None)


__all__ = [
    "LoginThrottle",
    "constant_time_equals",
    "hash_password",
    "needs_rehash",
    "new_token",
    "token_digest",
    "verify_password",
]
