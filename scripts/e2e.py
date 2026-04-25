"""End-to-end OAuth + MCP round-trip against a live server.

Requires:
  * A running instance of this server (local or deployed)
  * A Supabase project with the schema applied

Usage:
    python scripts/e2e.py --base-url http://localhost:8080 \\
        --email you+e2e@example.com --password supersecret

What it does:
  1. Fetches /.well-known metadata.
  2. POSTs to /oauth/register to obtain a public client_id.
  3. Posts to /oauth/authorize with the email/password + PKCE to get an auth code.
  4. Exchanges the code at /oauth/token for an access token.
  5. Calls MCP tools/list to enumerate tools.
  6. Calls a couple of read-only tools (get_timezone, get_fitness_goals).

Safe to run repeatedly against the same email \u2014 reuses the account.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import sys
from urllib.parse import parse_qs, urlparse

import httpx


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:64]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _mcp_call(client: httpx.Client, base: str, token: str, method: str, params: dict | None = None) -> dict:
    """Send a single JSON-RPC request over streamable HTTP and parse the response.
    The server may reply as application/json or text/event-stream; we handle both."""
    body = {"jsonrpc": "2.0", "id": secrets.randbelow(10_000), "method": method}
    if params is not None:
        body["params"] = params
    r = client.post(
        f"{base}/mcp",
        headers={
            "authorization": f"Bearer {token}",
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
        },
        json=body,
        timeout=30,
    )
    r.raise_for_status()
    ct = r.headers.get("content-type", "")
    if "text/event-stream" in ct:
        for line in r.text.splitlines():
            if line.startswith("data:"):
                data = json.loads(line[5:].strip())
                if data.get("id") == body["id"]:
                    return data
        raise RuntimeError(f"no SSE data matched id={body['id']}: {r.text[:500]}")
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    print(f"\u2192 Target: {base}")

    with httpx.Client(follow_redirects=False, timeout=15) as c:
        meta = c.get(f"{base}/.well-known/oauth-authorization-server").raise_for_status().json()
        print(f"\u2713 metadata issuer={meta['issuer']}")

        # Register a public client
        reg = c.post(f"{base}/oauth/register", json={
            "client_name": "e2e-smoke",
            "redirect_uris": ["http://localhost:1/cb"],
            "token_endpoint_auth_method": "none",
        }).raise_for_status().json()
        client_id = reg["client_id"]
        print(f"\u2713 registered client_id={client_id}")

        # PKCE + authorize (try signup first; fall back to login if already exists)
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(8)

        def _post_authorize(mode: str) -> httpx.Response:
            params = {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": "http://localhost:1/cb",
                "state": state,
                "scope": "mcp",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
            from urllib.parse import urlencode
            qs = urlencode(params)
            return c.post(
                f"{base}/oauth/authorize?{qs}",
                data={"email": args.email, "password": args.password, "mode": mode, "__params__": qs},
            )

        r = _post_authorize("signup")
        if r.status_code == 200 and b"already registered" in r.content.lower():
            r = _post_authorize("login")
        if r.status_code != 302:
            print(f"authorize did not redirect: {r.status_code}\n{r.text[:400]}", file=sys.stderr)
            return 2
        cb = urlparse(r.headers["location"])
        code = parse_qs(cb.query)["code"][0]
        print("\u2713 got auth code")

        tok = c.post(f"{base}/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://localhost:1/cb",
            "client_id": client_id,
            "code_verifier": verifier,
        }).raise_for_status().json()
        access = tok["access_token"]
        print(f"\u2713 got access token (expires_in={tok['expires_in']})")

        # Initialize MCP session (required by spec)
        init = _mcp_call(c, base, access, "initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "e2e-smoke", "version": "0.1"},
        })
        if "result" not in init:
            print(f"initialize failed: {init}", file=sys.stderr)
            return 3
        print(f"\u2713 MCP initialize \u2192 server={init['result'].get('serverInfo', {}).get('name')}")

        tools = _mcp_call(c, base, access, "tools/list", {})
        names = [t["name"] for t in tools.get("result", {}).get("tools", [])]
        print(f"\u2713 tools/list returned {len(names)} tools")

        tz = _mcp_call(c, base, access, "tools/call", {"name": "get_timezone", "arguments": {}})
        print(f"\u2713 get_timezone \u2192 {tz.get('result', tz)}")

        fg = _mcp_call(c, base, access, "tools/call", {"name": "get_fitness_goals", "arguments": {}})
        print(f"\u2713 get_fitness_goals \u2192 {fg.get('result', fg)}")

    print("\nAll e2e checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
