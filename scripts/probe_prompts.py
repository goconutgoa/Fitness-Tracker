"""Self-contained probe: hits a live MCP server, signs up a throwaway account,
and reports exactly what the server's `prompts/list` returns over the wire.

Usage:
    python scripts/probe_prompts.py --base-url https://your-deployment.example.com
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import sys
from urllib.parse import parse_qs, urlencode, urlparse

import httpx


def _pkce_pair() -> tuple[str, str]:
    v = secrets.token_urlsafe(64)[:64]
    c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, c


def _mcp_call(client: httpx.Client, base: str, token: str, method: str, params: dict) -> dict:
    body = {"jsonrpc": "2.0", "id": secrets.randbelow(10_000), "method": method, "params": params}
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
    if "text/event-stream" in r.headers.get("content-type", ""):
        for line in r.text.splitlines():
            if line.startswith("data:"):
                d = json.loads(line[5:].strip())
                if d.get("id") == body["id"]:
                    return d
        raise RuntimeError(f"no SSE data matched id={body['id']}")
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    email = f"probe_{secrets.token_hex(6)}@probe.local"
    password = secrets.token_urlsafe(16)
    print(f"[1] target: {base}")
    print(f"[2] using throwaway account: {email}")

    with httpx.Client(follow_redirects=False, timeout=30) as c:
        meta = c.get(f"{base}/.well-known/oauth-authorization-server").raise_for_status().json()
        print(f"[3] metadata issuer: {meta['issuer']}")

        reg = c.post(f"{base}/oauth/register", json={
            "client_name": "prompts-probe",
            "redirect_uris": ["http://localhost:1/cb"],
            "token_endpoint_auth_method": "none",
        }).raise_for_status().json()
        client_id = reg["client_id"]
        print(f"[4] registered client_id: {client_id}")

        verifier, challenge = _pkce_pair()
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://localhost:1/cb",
            "state": secrets.token_urlsafe(8),
            "scope": "mcp",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        qs = urlencode(params)
        r = c.post(
            f"{base}/oauth/authorize?{qs}",
            data={"email": email, "password": password, "mode": "signup", "__params__": qs},
        )
        if r.status_code != 302:
            print(f"[!] authorize failed: status={r.status_code}", file=sys.stderr)
            print(r.text[:600], file=sys.stderr)
            return 2
        code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
        print("[5] got auth code")

        tok = c.post(f"{base}/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://localhost:1/cb",
            "client_id": client_id,
            "code_verifier": verifier,
        }).raise_for_status().json()
        access = tok["access_token"]
        print("[6] got access token")

        init = _mcp_call(c, base, access, "initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "prompts-probe", "version": "1"},
        })
        caps = init["result"].get("capabilities", {})
        print(f"\n[7] SERVER CAPABILITIES: {sorted(caps.keys())}")
        print(f"    prompts: {caps.get('prompts')}")
        print(f"    tools  : {caps.get('tools')}")

        if "prompts" not in caps:
            print("\n*** DEPLOYED SERVER DOES NOT ADVERTISE 'prompts' CAPABILITY ***")
            print("*** This means Render is still running an old build.        ***")
            print("*** Fix: Render dashboard -> Manual Deploy -> Clear cache  ***")
        else:
            try:
                prompts = _mcp_call(c, base, access, "prompts/list", {})
                if "error" in prompts:
                    print(f"\n[8] prompts/list ERROR: {prompts['error']}")
                else:
                    pnames = [p["name"] for p in prompts.get("result", {}).get("prompts", [])]
                    print(f"\n[8] prompts/list: {len(pnames)} prompts returned")
                    for n in pnames:
                        print(f"    - {n}")
                    print(f"\n*** DEPLOYED SERVER IS HEALTHY. {len(pnames)} prompts present.")
                    print("*** If Claude doesn't show them, the issue is Claude's UI/cache, not the server. ***")
            except Exception as e:
                print(f"\n[!] prompts/list crashed: {e!r}", file=sys.stderr)
                return 3

        # Clean up: delete the throwaway account
        try:
            _mcp_call(c, base, access, "tools/call", {
                "name": "delete_account",
                "arguments": {"confirm": "DELETE"},
            })
            print("\n[9] cleaned up probe account")
        except Exception as e:
            print(f"\n[9] cleanup failed (manual cleanup may be needed): {e!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
