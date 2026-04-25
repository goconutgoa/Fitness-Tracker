"""Build the FastMCP server and register every tool. Exposed over
streamable-HTTP at ``/mcp`` (see ``src/app.py``)."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .tools import fitness as fitness_tools
from .tools import nutrition as nutrition_tools


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
    )
    nutrition_tools.register(mcp)
    fitness_tools.register(mcp)
    return mcp


mcp = build_mcp()
