"""Access tokens = short-lived HS256 JWTs. Refresh tokens are opaque strings
stored in Supabase so they can be revoked without keeping server-side state
about JWTs."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from ..config import get_settings

ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
AUTH_CODE_TTL = timedelta(minutes=10)


@dataclass
class AuthUser:
    user_id: str
    email: str
    client_id: str | None = None


def issue_access_token(user_id: str, email: str, client_id: str | None = None) -> tuple[str, int]:
    s = get_settings()
    now = datetime.now(timezone.utc)
    exp = now + ACCESS_TTL
    # Per MCP spec + RFC 8707, ``aud`` must be the canonical resource URL
    # (the MCP endpoint) so that resource-server validators can confirm
    # the token was minted for them. Claude.ai's connector is strict here.
    payload = {
        "sub": user_id,
        "email": email,
        "iss": s.issuer,
        "aud": f"{s.issuer}/mcp",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    if client_id:
        payload["client_id"] = client_id
    token = jwt.encode(payload, s.jwt_secret, algorithm="HS256")
    return token, int(ACCESS_TTL.total_seconds())


def verify_access_token(token: str) -> AuthUser | None:
    s = get_settings()
    try:
        payload = jwt.decode(
            token,
            s.jwt_secret,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except JWTError:
        return None
    uid = payload.get("sub")
    email = payload.get("email")
    if not uid or not email:
        return None
    return AuthUser(user_id=uid, email=email, client_id=payload.get("client_id"))


def new_opaque_token(n_bytes: int = 32) -> str:
    return secrets.token_urlsafe(n_bytes)
