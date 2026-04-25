"""ASGI middleware that validates ``Authorization: Bearer`` on MCP traffic
and sets the ``current_user`` ContextVar for the duration of the request."""
from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from ..context import current_user
from .tokens import verify_access_token


class BearerAuthMiddleware:
    """Wraps the MCP sub-app. Rejects unauthenticated traffic with 401."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")
        token = None
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()

        if not token:
            await _unauthorized(send)
            return

        user = verify_access_token(token)
        if not user:
            await _unauthorized(send)
            return

        tok = current_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            current_user.reset(tok)


async def _unauthorized(send: Send) -> None:
    body = b'{"error":"invalid_token","error_description":"Missing or invalid bearer token"}'
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"www-authenticate", b'Bearer realm="mcp"'),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})
