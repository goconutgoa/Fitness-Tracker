"""OAuth 2.0 authorization server — implements only what Claude.ai connectors
need:

  * RFC 8414 ``.well-known/oauth-authorization-server`` metadata
  * RFC 7591 dynamic client registration at ``/oauth/register``
  * Authorization-code + PKCE at ``/oauth/authorize``
  * Token exchange + refresh at ``/oauth/token``

Claude's connector flow:
    1. GETs the metadata doc.
    2. POSTs to ``/oauth/register`` to obtain a client_id.
    3. Opens ``/oauth/authorize`` in the user's browser.
    4. Server renders login/signup, authenticates, redirects back with ``code``.
    5. Claude exchanges ``code`` + ``code_verifier`` for tokens at ``/oauth/token``.
    6. Every MCP request carries ``Authorization: Bearer <access_token>``.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from ..config import get_settings
from ..db import get_supabase
from .passwords import hash_password, verify_password
from .tokens import AUTH_CODE_TTL, REFRESH_TTL, issue_access_token, new_opaque_token

SESSION_COOKIE = "mcp_session"


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="mcp-session")


def _set_session(resp: Response, user_id: str, email: str) -> None:
    signer = _signer()
    value = signer.dumps({"uid": user_id, "email": email})
    resp.set_cookie(
        SESSION_COOKIE,
        value,
        max_age=60 * 60 * 24 * 14,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _read_session(request: Request) -> dict | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        return _signer().loads(raw, max_age=60 * 60 * 24 * 14)
    except BadSignature:
        return None


def _verify_pkce(code_verifier: str, code_challenge: str, method: str) -> bool:
    if method == "plain":
        return code_verifier == code_challenge
    if method == "S256":
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return expected == code_challenge
    return False


# ---------------------------------------------------------------------------
# Metadata + dynamic client registration
# ---------------------------------------------------------------------------

async def metadata(request: Request) -> JSONResponse:
    s = get_settings()
    return JSONResponse(
        {
            "issuer": s.issuer,
            "authorization_endpoint": f"{s.issuer}/oauth/authorize",
            "token_endpoint": f"{s.issuer}/oauth/token",
            "registration_endpoint": f"{s.issuer}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256", "plain"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
            "scopes_supported": ["mcp"],
        }
    )


async def register_client(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)

    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)

    client_id = "c_" + secrets.token_urlsafe(16)
    auth_method = body.get("token_endpoint_auth_method", "none")
    client_secret = None if auth_method == "none" else secrets.token_urlsafe(32)

    sb = get_supabase()
    sb.table("oauth_clients").insert({
        "client_id": client_id,
        "client_secret": client_secret,
        "client_name": body.get("client_name") or "MCP Client",
        "redirect_uris": redirect_uris,
        "grant_types": body.get("grant_types") or ["authorization_code", "refresh_token"],
        "token_endpoint_auth_method": auth_method,
    }).execute()

    resp = {
        "client_id": client_id,
        "redirect_uris": redirect_uris,
        "client_name": body.get("client_name") or "MCP Client",
        "grant_types": body.get("grant_types") or ["authorization_code", "refresh_token"],
        "token_endpoint_auth_method": auth_method,
    }
    if client_secret:
        resp["client_secret"] = client_secret
    return JSONResponse(resp, status_code=201)


# ---------------------------------------------------------------------------
# /oauth/authorize — GET renders login, POST processes it
# ---------------------------------------------------------------------------

_LOGIN_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in — Fitness + Nutrition MCP</title>
<style>
:root{color-scheme:light dark}
body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0b0f14;color:#e6edf3;margin:0;min-height:100vh;display:grid;place-items:center}
.card{background:#121820;border:1px solid #242c36;border-radius:14px;padding:28px 32px;width:min(400px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.3)}
h1{margin:0 0 4px;font-size:22px}
p.sub{margin:0 0 20px;color:#96a0ad;font-size:14px}
label{display:block;font-size:13px;color:#b9c2cf;margin:12px 0 6px}
input{width:100%;padding:10px 12px;border-radius:8px;border:1px solid #2c3642;background:#0b0f14;color:#e6edf3;font-size:14px;box-sizing:border-box}
button{width:100%;margin-top:16px;padding:11px;border:0;border-radius:8px;background:#3b82f6;color:#fff;font-weight:600;font-size:14px;cursor:pointer}
button.ghost{background:transparent;border:1px solid #2c3642;color:#b9c2cf;margin-top:8px}
.err{background:#3a1a1d;border:1px solid #6b2a30;color:#ffb1b1;padding:8px 12px;border-radius:8px;font-size:13px;margin-top:10px}
.toggle{margin-top:14px;text-align:center;font-size:13px;color:#96a0ad}
.toggle a{color:#7ab2ff;text-decoration:none}
</style>
</head><body>
<form class="card" method="post">
  <h1>{{TITLE}}</h1>
  <p class="sub">{{SUB}}</p>
  {{ERROR}}
  <label>Email</label>
  <input type="email" name="email" required autofocus value="{{EMAIL}}">
  <label>Password</label>
  <input type="password" name="password" minlength="8" required>
  <input type="hidden" name="mode" value="{{MODE}}">
  <input type="hidden" name="__params__" value="{{PARAMS}}">
  <button type="submit">{{TITLE}}</button>
  <div class="toggle">{{TOGGLE}}</div>
</form>
</body></html>
"""


def _render_login(request: Request, params_qs: str, *, signup: bool, error: str | None, email: str = "") -> HTMLResponse:
    if signup:
        title, sub = "Create account", "One account, all your nutrition + fitness data."
        toggle = f'Already have an account? <a href="{request.url.path}?{params_qs}">Sign in</a>'
        mode = "signup"
    else:
        title, sub = "Sign in", "Connect Claude to your data."
        toggle = f'New here? <a href="{request.url.path}?{params_qs}&signup=1">Create an account</a>'
        mode = "login"
    err = f'<div class="err">{error}</div>' if error else ""
    html = (
        _LOGIN_HTML
        .replace("{{TITLE}}", title)
        .replace("{{SUB}}", sub)
        .replace("{{TOGGLE}}", toggle)
        .replace("{{MODE}}", mode)
        .replace("{{ERROR}}", err)
        .replace("{{EMAIL}}", email.replace('"', "&quot;"))
        .replace("{{PARAMS}}", params_qs.replace('"', "&quot;"))
    )
    return HTMLResponse(html)


def _authz_params(request: Request) -> dict:
    q = request.query_params
    return {
        "response_type": q.get("response_type", ""),
        "client_id": q.get("client_id", ""),
        "redirect_uri": q.get("redirect_uri", ""),
        "state": q.get("state", ""),
        "scope": q.get("scope", "mcp"),
        "code_challenge": q.get("code_challenge", ""),
        "code_challenge_method": q.get("code_challenge_method", ""),
    }


def _validate_client(client_id: str, redirect_uri: str) -> bool:
    sb = get_supabase()
    r = sb.table("oauth_clients").select("redirect_uris").eq("client_id", client_id).maybe_single().execute()
    if not r or not r.data:
        return False
    return redirect_uri in (r.data.get("redirect_uris") or [])


def _issue_code(user_id: str, params: dict) -> str:
    code = new_opaque_token(24)
    expires = datetime.now(timezone.utc) + AUTH_CODE_TTL
    get_supabase().table("oauth_auth_codes").insert({
        "code": code,
        "client_id": params["client_id"],
        "user_id": user_id,
        "redirect_uri": params["redirect_uri"],
        "scope": params.get("scope") or "mcp",
        "code_challenge": params.get("code_challenge") or None,
        "code_challenge_method": params.get("code_challenge_method") or None,
        "expires_at": expires.isoformat(),
    }).execute()
    return code


async def authorize(request: Request) -> Response:
    params = _authz_params(request)
    if params["response_type"] != "code":
        return JSONResponse({"error": "unsupported_response_type"}, status_code=400)
    if not params["client_id"] or not params["redirect_uri"]:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    if not _validate_client(params["client_id"], params["redirect_uri"]):
        return JSONResponse({"error": "invalid_client_or_redirect"}, status_code=400)

    params_qs = urlencode({k: v for k, v in params.items() if v})
    signup = request.query_params.get("signup") == "1"

    if request.method == "GET":
        session = _read_session(request)
        if session:
            code = _issue_code(session["uid"], params)
            sep = "&" if "?" in params["redirect_uri"] else "?"
            url = f"{params['redirect_uri']}{sep}code={code}"
            if params["state"]:
                url += f"&state={params['state']}"
            return RedirectResponse(url, status_code=302)
        return _render_login(request, params_qs, signup=signup, error=None)

    form = await request.form()
    email = (form.get("email") or "").strip().lower()
    password = form.get("password") or ""
    mode = form.get("mode") or "login"

    sb = get_supabase()
    if mode == "signup":
        existing = sb.table("app_users").select("id").eq("email", email).maybe_single().execute()
        if existing and existing.data:
            return _render_login(request, params_qs, signup=True, error="Email is already registered.", email=email)
        if len(password) < 8:
            return _render_login(request, params_qs, signup=True, error="Password must be at least 8 characters.", email=email)
        ins = sb.table("app_users").insert({"email": email, "password_hash": hash_password(password)}).execute()
        user = ins.data[0]
        # empty default goal rows so read endpoints can always return a shape
        sb.table("nutrition_goals").upsert({"user_id": user["id"]}).execute()
        sb.table("fitness_goals").upsert({"user_id": user["id"]}).execute()
    else:
        row = sb.table("app_users").select("id,email,password_hash").eq("email", email).maybe_single().execute()
        if not row or not row.data or not verify_password(password, row.data["password_hash"]):
            return _render_login(request, params_qs, signup=False, error="Invalid email or password.", email=email)
        user = row.data
        sb.table("app_users").update({"last_login_at": datetime.now(timezone.utc).isoformat()}).eq("id", user["id"]).execute()

    code = _issue_code(user["id"], params)
    sep = "&" if "?" in params["redirect_uri"] else "?"
    url = f"{params['redirect_uri']}{sep}code={code}"
    if params["state"]:
        url += f"&state={params['state']}"
    resp = RedirectResponse(url, status_code=302)
    _set_session(resp, user["id"], user["email"])
    return resp


# ---------------------------------------------------------------------------
# /oauth/token — authorization_code + refresh_token
# ---------------------------------------------------------------------------

async def token(request: Request) -> JSONResponse:
    form = await request.form()
    grant_type = form.get("grant_type")
    client_id = form.get("client_id")
    client_secret = form.get("client_secret")

    if not grant_type or not client_id:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    sb = get_supabase()
    cli = sb.table("oauth_clients").select("*").eq("client_id", client_id).maybe_single().execute()
    if not cli or not cli.data:
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    if cli.data["token_endpoint_auth_method"] != "none":
        if client_secret != cli.data.get("client_secret"):
            return JSONResponse({"error": "invalid_client"}, status_code=401)

    if grant_type == "authorization_code":
        code = form.get("code")
        redirect_uri = form.get("redirect_uri")
        code_verifier = form.get("code_verifier")
        if not code or not redirect_uri:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        row = sb.table("oauth_auth_codes").select("*").eq("code", code).maybe_single().execute()
        if not row or not row.data:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        rec = row.data
        if rec["consumed"] or rec["client_id"] != client_id or rec["redirect_uri"] != redirect_uri:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if datetime.fromisoformat(rec["expires_at"].replace("Z", "+00:00")) < datetime.now(timezone.utc):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        if rec.get("code_challenge"):
            if not code_verifier or not _verify_pkce(code_verifier, rec["code_challenge"], rec["code_challenge_method"] or "plain"):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)

        sb.table("oauth_auth_codes").update({"consumed": True}).eq("code", code).execute()
        user = sb.table("app_users").select("id,email").eq("id", rec["user_id"]).maybe_single().execute().data
        return _issue_token_pair(user, client_id, rec.get("scope") or "mcp")

    if grant_type == "refresh_token":
        refresh = form.get("refresh_token")
        if not refresh:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        row = sb.table("oauth_refresh_tokens").select("*").eq("token", refresh).maybe_single().execute()
        if not row or not row.data:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        rec = row.data
        if rec["revoked"] or rec["client_id"] != client_id:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if datetime.fromisoformat(rec["expires_at"].replace("Z", "+00:00")) < datetime.now(timezone.utc):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        sb.table("oauth_refresh_tokens").update({"revoked": True}).eq("token", refresh).execute()
        user = sb.table("app_users").select("id,email").eq("id", rec["user_id"]).maybe_single().execute().data
        return _issue_token_pair(user, client_id, rec.get("scope") or "mcp")

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


def _issue_token_pair(user: dict, client_id: str, scope: str) -> JSONResponse:
    access, ttl = issue_access_token(user["id"], user["email"], client_id)
    refresh = new_opaque_token(32)
    get_supabase().table("oauth_refresh_tokens").insert({
        "token": refresh,
        "client_id": client_id,
        "user_id": user["id"],
        "scope": scope,
        "expires_at": (datetime.now(timezone.utc) + REFRESH_TTL).isoformat(),
    }).execute()
    return JSONResponse({
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": ttl,
        "refresh_token": refresh,
        "scope": scope,
    })


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

oauth_routes = [
    Route("/.well-known/oauth-authorization-server", metadata, methods=["GET"]),
    Route("/.well-known/openid-configuration", metadata, methods=["GET"]),
    Route("/oauth/register", register_client, methods=["POST"]),
    Route("/oauth/authorize", authorize, methods=["GET", "POST"]),
    Route("/oauth/token", token, methods=["POST"]),
]
