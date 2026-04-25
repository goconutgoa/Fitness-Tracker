"""Offline smoke test — no Supabase required.

Verifies:
  1. App imports cleanly.
  2. All expected MCP tools are registered.
  3. OAuth metadata endpoint returns a well-formed document.
  4. /mcp rejects unauthenticated traffic with 401.
  5. An invalid JWT is rejected.
  6. A JWT minted with the local secret is accepted by the auth middleware.

Run:
    python scripts/smoke.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Stub env BEFORE importing the app so config loads.
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "stub-service-key")
os.environ.setdefault("JWT_SECRET", "smoke-test-jwt-secret-please-rotate")
os.environ.setdefault("SESSION_SECRET", "smoke-test-session-secret-please-rotate")
os.environ.setdefault("PUBLIC_BASE_URL", "http://localhost:8080")


EXPECTED_TOOLS = {
    # nutrition
    "log_meal", "update_meal", "delete_meal",
    "get_meals_today", "get_meals_by_date", "get_meals_by_date_range",
    "log_water", "delete_water", "get_water_today", "get_water_by_date",
    "set_nutrition_goals", "get_nutrition_goals", "get_goal_progress",
    "get_nutrition_summary", "get_trends", "get_meal_patterns",
    "get_timezone", "set_timezone", "delete_account",
    # fitness
    "search_exercises", "add_custom_exercise",
    "log_workout", "add_sets_to_workout", "delete_workout", "delete_set",
    "get_workouts_today", "get_workouts_by_date", "get_workouts_by_date_range",
    "log_cardio", "delete_cardio", "get_cardio_by_date",
    "log_steps", "get_steps_today", "get_steps_by_date_range",
    "log_body_metrics", "get_body_metrics_history",
    "set_fitness_goals", "get_fitness_goals", "get_fitness_progress",
    "get_exercise_history", "get_personal_records", "get_workout_trends",
    "get_workout_patterns", "get_consistency_streak", "get_fitness_summary",
}


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"ok   {msg}")


class _LifespanRunner:
    """Run an ASGI app's lifespan (httpx.ASGITransport doesn't do this on
    its own)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __aenter__(self):
        self._recv: asyncio.Queue = asyncio.Queue()
        self._send: asyncio.Queue = asyncio.Queue()
        self._task = asyncio.create_task(self.app({"type": "lifespan"}, self._recv.get, self._send.put))
        await self._recv.put({"type": "lifespan.startup"})
        msg = await self._send.get()
        if msg["type"] != "lifespan.startup.complete":
            raise RuntimeError(f"startup failed: {msg}")
        return self

    async def __aexit__(self, *exc):
        await self._recv.put({"type": "lifespan.shutdown"})
        try:
            await asyncio.wait_for(self._send.get(), timeout=5)
        except asyncio.TimeoutError:
            pass
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass


async def main() -> None:
    from src.app import app
    from src.auth.tokens import issue_access_token
    from src.mcp_server import mcp

    _ok("app + mcp_server import cleanly")

    # 1. Tools are registered
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    missing = EXPECTED_TOOLS - names
    extra = names - EXPECTED_TOOLS
    if missing:
        _fail(f"missing tools: {sorted(missing)}")
    if extra:
        print(f"  note: {len(extra)} extra tools registered: {sorted(extra)}")
    _ok(f"{len(names)} MCP tools registered (all expected present)")

    # 2. In-process ASGI probes via httpx
    try:
        import httpx
    except ImportError:
        _fail("httpx not installed \u2014 run: pip install -r requirements.txt")

    transport = httpx.ASGITransport(app=app)
    async with _LifespanRunner(app), httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/healthz")
        if r.status_code != 200 or r.json().get("status") != "ok":
            _fail(f"/healthz unexpected response: {r.status_code} {r.text!r}")
        _ok("/healthz returns ok")

        r = await client.get("/.well-known/oauth-authorization-server")
        if r.status_code != 200:
            _fail(f"metadata not 200: {r.status_code}")
        meta = r.json()
        for key in ("issuer", "authorization_endpoint", "token_endpoint",
                    "registration_endpoint", "response_types_supported",
                    "grant_types_supported", "code_challenge_methods_supported"):
            if key not in meta:
                _fail(f"metadata missing {key}")
        _ok("OAuth metadata document is well-formed")

        # /mcp without auth → 401 + WWW-Authenticate
        r = await client.post("/mcp", json={}, headers={"accept": "application/json, text/event-stream"})
        if r.status_code != 401:
            _fail(f"/mcp without auth expected 401, got {r.status_code}")
        if "bearer" not in r.headers.get("www-authenticate", "").lower():
            _fail("missing WWW-Authenticate: Bearer on 401")
        _ok("/mcp rejects unauthenticated traffic (401 + WWW-Authenticate)")

        # /mcp with garbage token → 401
        r = await client.post("/mcp", json={}, headers={
            "authorization": "Bearer not-a-real-jwt",
            "accept": "application/json, text/event-stream",
        })
        if r.status_code != 401:
            _fail(f"/mcp with bad token expected 401, got {r.status_code}")
        _ok("/mcp rejects invalid bearer tokens")

        # /mcp with valid JWT → middleware accepts (MCP layer will then 400/406/etc.
        # because we didn't send a real JSON-RPC body, but that's past auth).
        token, _ttl = issue_access_token("00000000-0000-0000-0000-000000000001", "smoke@test.local", "smoke")
        r = await client.post("/mcp", json={}, headers={
            "authorization": f"Bearer {token}",
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
        })
        if r.status_code == 401:
            _fail("/mcp rejected a valid JWT (middleware bug)")
        _ok(f"/mcp accepts valid bearer (post-auth status={r.status_code})")

    print("\nAll smoke checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
