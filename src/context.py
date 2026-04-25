"""Per-request authenticated user, carried across async call boundaries via
a ContextVar. The ASGI bearer-token middleware sets this before handing off
to the MCP streamable-HTTP app, and tool functions read it."""
from __future__ import annotations

from contextvars import ContextVar

from .auth.tokens import AuthUser

current_user: ContextVar[AuthUser | None] = ContextVar("current_user", default=None)


def require_user() -> AuthUser:
    u = current_user.get()
    if u is None:
        raise PermissionError("Not authenticated. Reconnect this MCP connector.")
    return u
