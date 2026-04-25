"""Starlette application that composes:
  * OAuth 2.0 provider routes (/oauth/*, /.well-known/*)
  * MCP streamable-HTTP sub-app mounted at /mcp, guarded by Bearer-token middleware
  * A landing page + health check
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from .auth.middleware import BearerAuthMiddleware
from .auth.oauth import oauth_routes
from .config import get_settings
from .mcp_server import mcp


async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


_LANDING = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fitness + Nutrition MCP</title>
<style>
:root{color-scheme:light dark}
body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0b0f14;color:#e6edf3;margin:0;min-height:100vh}
.wrap{max-width:780px;margin:0 auto;padding:64px 24px}
h1{font-size:36px;margin:0 0 8px}
p.lead{color:#96a0ad;font-size:17px;margin:0 0 28px}
code{background:#121820;padding:2px 6px;border-radius:4px;border:1px solid #242c36;font-size:14px}
.card{background:#121820;border:1px solid #242c36;border-radius:12px;padding:20px 24px;margin:14px 0}
h2{font-size:18px;margin:0 0 10px}
ol{margin:0;padding-left:20px}
li{margin:6px 0}
.badge{display:inline-block;background:#1c2530;color:#7ab2ff;padding:2px 8px;border-radius:999px;font-size:12px;margin-left:8px}
</style></head>
<body><div class="wrap">
<h1>Fitness + Nutrition MCP <span class="badge">v0.1</span></h1>
<p class="lead">Personal nutrition + full fitness tracking as a remote MCP server. Log meals, water, workouts, cardio, steps, body metrics — and surface progressive improvement over time.</p>
<div class="card">
  <h2>Connect from Claude.ai</h2>
  <ol>
    <li>Open <b>Settings → Connectors → Add custom connector</b>.</li>
    <li>Paste this URL: <code>{BASE}/mcp</code></li>
    <li>Sign in (or create an account) when the OAuth prompt appears.</li>
  </ol>
</div>
<div class="card">
  <h2>Endpoints</h2>
  <ul>
    <li><code>GET /.well-known/oauth-authorization-server</code> — server metadata</li>
    <li><code>POST /oauth/register</code> — dynamic client registration</li>
    <li><code>GET/POST /oauth/authorize</code> — login + consent</li>
    <li><code>POST /oauth/token</code> — token exchange + refresh</li>
    <li><code>POST /mcp</code> — MCP streamable HTTP transport (Bearer auth required)</li>
  </ul>
</div>
</div></body></html>
"""


async def landing(_: Request) -> HTMLResponse:
    return HTMLResponse(_LANDING.replace("{BASE}", get_settings().issuer))


def build_app() -> ASGIApp:
    """Top-level ASGI app.

    The MCP streamable-HTTP endpoint lives at ``/mcp`` (and ``/mcp/``).
    Everything else routes through Starlette. We dispatch manually at the
    root so the connector URL works both with and without a trailing
    slash (Starlette's ``Mount`` only matches the trailing-slash form,
    and enabling redirect-slashes drops the ``Authorization`` header on
    some clients)."""
    routes = [
        Route("/", landing),
        Route("/healthz", healthz),
        *oauth_routes,
    ]

    @asynccontextmanager
    async def lifespan(_app):
        # FastMCP's streamable-HTTP session manager must be entered as a
        # context manager before any request arrives; otherwise the inner
        # task group isn't initialized.
        async with mcp.session_manager.run():
            yield

    inner_starlette = Starlette(routes=routes, lifespan=lifespan)
    mcp_handler = BearerAuthMiddleware(mcp.streamable_http_app())

    async def dispatch(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            # Lifespan events must go through Starlette so the context
            # manager above is entered/exited.
            await inner_starlette(scope, receive, send)
            return
        if scope["type"] != "http":
            await inner_starlette(scope, receive, send)
            return
        path = scope.get("path", "")
        if path == "/mcp" or path.startswith("/mcp/"):
            new_scope = dict(scope)
            # Strip the /mcp prefix so the inner MCP Starlette (internal path "/")
            # routes correctly. Works for both /mcp and /mcp/anything.
            stripped = path[len("/mcp"):] or "/"
            new_scope["path"] = stripped
            new_scope["raw_path"] = stripped.encode()
            new_scope["root_path"] = scope.get("root_path", "") + "/mcp"
            await mcp_handler(new_scope, receive, send)
            return
        await inner_starlette(scope, receive, send)

    return dispatch


app = build_app()
