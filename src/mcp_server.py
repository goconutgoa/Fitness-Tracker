"""Build the FastMCP server and register every tool. Exposed over
streamable-HTTP at ``/mcp`` (see ``src/app.py``)."""
from __future__ import annotations

from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import get_settings
from .prompts import templates as prompt_templates
from .tools import fitness as fitness_tools
from .tools import nutrition as nutrition_tools
from .tools import workflows as workflow_tools


def _transport_security() -> TransportSecuritySettings:
    """Whitelist the public hostname. FastMCP's default DNS-rebinding
    guard only allows localhost/127.0.0.1, so any deployment behind a
    real domain hits the guard and 421s."""
    public = urlparse(get_settings().public_base_url)
    host = public.netloc or public.path  # netloc is empty for bare hosts
    allowed_hosts = ["localhost", "127.0.0.1", "localhost:8080", "127.0.0.1:8080"]
    allowed_origins = ["http://localhost:8080", "http://127.0.0.1:8080"]
    if host:
        allowed_hosts.append(host)
        allowed_origins.append(f"{public.scheme or 'https'}://{host}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def build_mcp() -> FastMCP:
    mcp = FastMCP(
        name="fitness-nutrition-mcp",
        instructions=(
            "You are a personal nutrition and fitness tracker. Use these tools to log the user's "
            "meals, water, workouts (sets/reps/weight), cardio, steps, and body metrics. Surface "
            "progressive improvement over time: compare recent vs prior windows, call out PR changes, "
            "and check progress against stated goals. Prefer local dates; the server already "
            "converts from the user's saved timezone. Always confirm destructive actions."
        ),
        stateless_http=True,
        streamable_http_path="/",   # outer mount at /mcp provides the prefix
        transport_security=_transport_security(),
    )
    nutrition_tools.register(mcp)
    fitness_tools.register(mcp)
    workflow_tools.register(mcp)
    prompt_templates.register(mcp)
    return mcp


mcp = build_mcp()
