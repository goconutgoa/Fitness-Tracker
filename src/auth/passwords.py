"""bcrypt password hashing.

We use the ``bcrypt`` package directly rather than passlib — passlib is
unmaintained and broke in bcrypt 5.x (its internal backend probe trips
the new long-password guard).
"""
from __future__ import annotations

import bcrypt

# bcrypt has a hard 72-byte limit on the password input. We truncate at the
# byte boundary so very long passphrases still hash deterministically.
_MAX_BYTES = 72


def _clamp(pw: str) -> bytes:
    encoded = pw.encode("utf-8")
    if len(encoded) > _MAX_BYTES:
        encoded = encoded[:_MAX_BYTES]
    return encoded


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(_clamp(pw), bcrypt.gensalt()).decode("utf-8")


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_clamp(pw), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
