# Study Guide — Fitness + Nutrition MCP

A complete walkthrough of this project for interview preparation and personal growth.
Includes the architecture, decisions, production bugs we shipped through, anticipated
interview questions, a file-by-file reading order, and a curriculum of skills, tools,
specs, and concepts to master.

> Maintainer note: this document was authored after the project shipped its first
> working build. Update it whenever you make a non-trivial change to architecture,
> auth, or the tool surface.

---

## Table of contents

- [Part I — Project brief](#part-i--project-brief)
  - [1. Elevator pitch](#1-the-elevator-pitch-the-line-you-should-be-able-to-give-in-20-seconds)
  - [2. Architecture](#2-the-architecture)
  - [3. End-to-end flow](#3-the-end-to-end-flow)
  - [4. Stack choices and why](#4-stack-choices--and-why)
  - [5. Database design](#5-database-design)
  - [6. The OAuth 2.0 flow](#6-the-oauth-20-flow-the-part-interviewers-will-dig-into)
  - [7. Production bugs we shipped through](#7-the-bugs-we-shipped-through-most-valuable-section-for-interviews)
  - [8. Code organization](#8-code-organization-file-by-file)
  - [9. Likely interview questions](#9-likely-interview-questions-and-sample-answers)
  - [10. Numbers you can quote on a resume](#10-numbers-you-can-quote-on-your-resume)
- [Part II — File-by-file reading guide](#part-ii--file-by-file-reading-guide)
  - [Phase 1 — The map](#phase-1--the-map-15-minutes)
  - [Phase 2 — Boot mechanics](#phase-2--boot-mechanics-5-minutes)
  - [Phase 3 — Request flow](#phase-3--request-flow-25-minutes)
  - [Phase 4 — OAuth](#phase-4--oauth-30-minutes--densest)
  - [Phase 5 — MCP server](#phase-5--mcp-server-20-minutes)
  - [Phase 6 — Tests](#phase-6--tests-15-minutes)
  - [Phase 7 — Operations](#phase-7--operations-skim-5-minutes)
- [Part III — Cross-cutting curriculum](#part-iii--cross-cutting-curriculum)
- [Part IV — Suggested 4-week study plan](#part-iv--suggested-4-week-study-plan)
- [Appendix A — Database deep dive](#appendix-a--database-deep-dive-supabase--postgres)
  - [A.1 Big picture (ER diagram)](#a1-big-picture-entity-relationship-diagram)
  - [A.2 Three logical clusters](#a2-the-three-logical-clusters)
  - [A.3 Table-by-table deep dive](#a3-table-by-table-deep-dive)
  - [A.4 Cross-cutting design rules](#a4-cross-cutting-design-rules)
  - [A.5 Read patterns and which index serves them](#a5-read-patterns-and-which-index-serves-them)
  - [A.6 Common admin queries](#a6-common-admin-queries-cheat-sheet)
  - [A.7 Why these design choices](#a7-why-these-design-choices--a-defendable-list-for-interviews)
  - [A.8 What scaling further would look like](#a8-what-scaling-further-would-look-like)

---

# Part I — Project brief

## 1. The elevator pitch (the line you should be able to give in 20 seconds)

> *"It's a remote MCP server I built in Python that lets Claude track my nutrition and
> full fitness data conversationally. It exposes 45 tools — meal logging, workouts with
> sets/reps/weight, cardio, steps, body metrics, PR tracking, longitudinal trends — over
> MCP's streamable-HTTP transport, gated by an OAuth 2.0 authorization-code flow with
> PKCE. It's deployed on Render with Supabase as the backing store, and Claude.ai
> connects to it as a custom connector."*

If they ask "MCP?": "Model Context Protocol — the open spec for how LLM clients call
external tools. Claude, Cursor, others speak it."

---

## 2. The architecture

```
                  +------------------+
                  |   Claude.ai      |
                  | (MCP client)     |
                  +--------+---------+
                           | HTTPS
                           v
              +-------------------------+
              | Render (Docker)         |
              | +---------------------+ |       +--------------+
              | | Starlette (ASGI)    |-+------>|  Supabase    |
              | |  - OAuth routes     | |       |  (Postgres + |
              | |  - Bearer middleware| |       |   RLS)       |
              | |  - FastMCP /mcp     | |       +--------------+
              | +---------------------+ |
              +-------------------------+
```

**Three things to remember:**

1. **Claude is the client.** Our server speaks MCP back to it.
2. **Auth happens once.** The browser-based OAuth dance produces a JWT bearer token
   Claude stores; every later tool call is just `Authorization: Bearer ...`.
3. **All app logic, including the OAuth provider, is in one Python ASGI app.** No
   separate auth service.

---

## 3. The end-to-end flow

What happens when someone types *"log my bench press today: 80kg x 5,5,4"* in Claude:

```
User -> Claude.ai -> POST /mcp                    {tools/call log_workout, args}
                     Authorization: Bearer eyJ...
                                  v
            BearerAuthMiddleware decodes JWT
            -> sets ContextVar `current_user`
                                  v
            FastMCP routes JSON-RPC to log_workout()
                                  v
            log_workout reads current_user, inserts:
            INSERT INTO workouts(user_id, ...)
            INSERT INTO workout_sets (x3, denormalized exercise_name)
                                  v
            Returns workout JSON to Claude
                                  v
            Claude shows confirmation to user
```

The clever part: `current_user` lives in a `ContextVar`, so tool functions don't need a
`request` parameter — they just call `require_user()`. The middleware sets it before
invoking the inner app and resets it after, guaranteeing isolation between concurrent
requests.

---

## 4. Stack choices — and why

| Choice | Why |
|---|---|
| **Python 3.12** | Type hints, modern asyncio, MCP SDK targets >=3.10 |
| **FastMCP** (official MCP SDK) | Decorator-based tool registration, streamable-HTTP transport built in. Alternative was hand-rolling JSON-RPC over HTTP — needless work. |
| **Starlette + Uvicorn** | Lightweight ASGI. We needed a *Python* HTTP framework on top of FastMCP for the OAuth routes. FastAPI would be heavier for what's essentially routing + middleware. |
| **Supabase (Postgres)** | Free tier, managed, has RLS so even if a query escapes user_id filtering the database refuses. We use the `service_role` key from a trusted server, but RLS is defense-in-depth. |
| **JWT (HS256) for access tokens** | Stateless verification — no DB round-trip per `/mcp` request. Refresh tokens are *opaque* and DB-stored so they're revocable. Standard hybrid pattern. |
| **bcrypt** for passwords | Industry standard. We initially used `passlib[bcrypt]` but bcrypt 5.x broke passlib (see Bug 4 below); switched to native `bcrypt`. |
| **Render free tier** | Free, Docker-native, automatic HTTPS. Tradeoff: sleeps after 15 min idle. Acceptable for a personal/showcase project. |
| **Single-file HTML in Python** (login form, landing page) | Avoided pulling in templating + static-file plumbing for two pages. Simpler ops surface. |

---

## 5. Database design

10 user-data tables + 3 OAuth tables + 1 accounts table. Highlights:

```
app_users                  <- email + bcrypt password_hash + IANA timezone
oauth_clients              <- dynamically registered Claude clients
oauth_auth_codes           <- short-lived (10 min) PKCE codes
oauth_refresh_tokens       <- opaque, revocable, 30-day TTL

meals, water_logs, nutrition_goals
exercises (seeded catalog of 28 + user-custom)
workouts -> workout_sets    <- parent/child
cardio_sessions
step_logs (one row per user-day)
body_metrics
fitness_goals
```

**Two design choices worth defending in an interview:**

1. **`workout_sets.exercise_name` is denormalized** (also has `exercise_id`).
   Why: PR queries and exercise-history filters are by name. Avoiding a join on every
   `get_exercise_history` call. Tradeoff: rename of an exercise won't propagate. For a
   personal tracker that's fine.

2. **`step_logs` uses `(user_id, log_date)` as a composite primary key** instead of
   an autogenerated id. Steps are inherently per-day; you upsert if you re-log. This
   makes the data model match the real-world semantics.

**RLS:** every per-user table has policies like `using (user_id = auth.uid())`. We
don't actually use anon-key access from clients, but if we ever did, RLS guarantees a
leak in app code can't expose another user's data.

---

## 6. The OAuth 2.0 flow (the part interviewers will dig into)

Claude.ai needs **OAuth 2.0 with PKCE + dynamic client registration**. We implemented
all of it ourselves on top of Supabase tables. Here's the dance:

```
1. Claude -> GET /.well-known/oauth-protected-resource         (RFC 9728)
   We respond: "this resource is protected by <issuer>"

2. Claude -> GET /.well-known/oauth-authorization-server       (RFC 8414)
   We respond with our authorize/token/register endpoints

3. Claude -> POST /oauth/register                              (RFC 7591)
   {redirect_uris, token_endpoint_auth_method:"none"}
   We mint client_id, store row, return it

4. Claude opens the user's browser to:
   GET /oauth/authorize?response_type=code
                       &client_id=...
                       &redirect_uri=https://claude.ai/api/mcp/auth_callback
                       &state=...
                       &code_challenge=BASE64URL(SHA256(verifier))   <- PKCE
                       &code_challenge_method=S256

5. Server renders signup/login HTML. User submits.
   - signup: bcrypt-hash password, INSERT into app_users
   - login : SELECT row, bcrypt verify
   Then INSERT into oauth_auth_codes (code, user_id, code_challenge, expires_at)
   302 redirect to redirect_uri?code=...&state=...

6. Claude -> POST /oauth/token
   {grant_type:"authorization_code", code, code_verifier, client_id}
   We:
   - lookup code -> check not consumed, not expired, client matches
   - PKCE: verify SHA256(code_verifier) base64url-equals stored challenge
   - mark consumed
   - issue HS256 JWT (sub=user_id, aud=<issuer>/mcp, exp=now+1h)
   - issue opaque refresh token, store in oauth_refresh_tokens
   Return both

7. Every later request:
   POST /mcp  Authorization: Bearer <jwt>
   BearerAuthMiddleware verifies signature + exp,
   sets ContextVar, hands off to FastMCP.
```

**PKCE (Proof Key for Code Exchange) — why we need it:** Claude is a *public client*
(no secret). Without PKCE, anyone who intercepts the auth code in a browser redirect
could exchange it for tokens. PKCE binds the code to a one-time random `verifier` only
the original client knows. We support both `S256` (SHA-256 hash) and `plain` (literal
compare). **In production only S256 should be honored** — keeping `plain` in the
metadata is a small concession to client compatibility.

---

## 7. The bugs we shipped through (most valuable section for interviews)

This is the section that proves you actually built it rather than copy-pasted.
Practice these — interviewers love hearing about real production problems.

### Bug 1: Empty directories killed the Docker build
**Symptom:** Render build failed at `COPY templates/ ./templates/` — *"not found"*.
**Cause:** I scaffolded empty `templates/` and `static/` folders, but the login HTML
was inlined into Python. Git doesn't track empty directories, so they didn't exist in
the build context.
**Fix:** Removed the unused `COPY` lines.
**Lesson:** Build context is a snapshot of what git tracks, not what's on your local
disk.

### Bug 2: Starlette's `Mount("/mcp")` only matched `/mcp/` (trailing slash)
**Symptom:** Locally, `POST /mcp` -> 404.
**Cause:** Starlette's `Mount` compiles a path regex that requires the trailing slash.
The default `redirect_slashes` would have 307'd `/mcp` -> `/mcp/`, but redirects strip
the `Authorization` header in many clients.
**Fix:** Replaced `Mount` with a small top-level ASGI dispatcher that routes both
`/mcp` and `/mcp/...` to the FastMCP app, rewriting the path before handoff.
**Lesson:** Auth headers don't always survive redirects. When the MCP spec says
"POST to /mcp," it means exactly that.

### Bug 3: FastMCP's session manager wasn't initialized
**Symptom:** First request -> `RuntimeError: Task group is not initialized. Make sure
to use run().`
**Cause:** FastMCP's streamable-HTTP transport opens an internal task group inside its
lifespan. By replacing `Mount` with a custom dispatcher, I'd skipped the lifespan
plumbing.
**Fix:** Added an `@asynccontextmanager` lifespan that does `async with
mcp.session_manager.run()` and routed `lifespan` ASGI events through Starlette so it
fires.
**Lesson:** ASGI has three scope types (`http`, `websocket`, `lifespan`). Custom
routing has to handle all three.

### Bug 4: passlib + bcrypt 5.x silently incompatible
**Symptom:** Signup -> 500 on Render. Stack trace: `ValueError: password cannot be
longer than 72 bytes` raised from inside passlib's *initialization probe*, not the
user's password.
**Cause:** bcrypt 4 silently truncated passwords > 72 bytes. bcrypt 5 made it a hard
error. passlib's `set_backend()` runs an internal probe that uses a 100-byte test
string — it explodes the moment you call `hash_password()` for the first time. passlib
hasn't shipped a fix because it's effectively unmaintained.
**Fix:** Dropped passlib, used the `bcrypt` package directly. Added explicit byte-clamp
at 72.
**Lesson:** When a stable library hasn't released in years, watch its transitive
dependencies. Pinning would have been a Band-Aid; replacing was correct.

### Bug 5: Used Supabase's anon key instead of service_role
**Symptom:** `POST /oauth/register` -> 500. Logs: *"new row violates row-level security
policy for table oauth_clients"*.
**Cause:** Supabase issues two API keys: `anon` (public, RLS enforced) and
`service_role` (server-side, bypasses RLS). Both start with `eyJhbGci...`, so they're
indistinguishable visually. I'd pasted the wrong one.
**Fix:** Decoded the JWT — confirmed `role: "anon"`, swapped for `role: "service_role"`.
**Lesson:** When you have two credentials that look identical, write a one-liner that
prints which one you have. Saved 30 minutes of guessing.

### Bug 6: Missing RFC 9728 protected-resource metadata
**Symptom:** Claude.ai: *"Couldn't reach the MCP server."* But our server was 100%
healthy on every endpoint we'd thought to add.
**Cause:** The latest MCP auth spec layers on RFC 9728. When a client gets 401 from
`/mcp`, it expects a `WWW-Authenticate` header containing `resource_metadata="..."`
pointing to a discovery document at `/.well-known/oauth-protected-resource`. Without
that, Claude can't connect the protected resource to its authorization server.
**Fix:** Added `/.well-known/oauth-protected-resource` returning
`{resource, authorization_servers}`, and updated the 401 response to include the
`resource_metadata` link.
**Lesson:** OAuth-protected MCP isn't just "OAuth + MCP" — it has its own discovery
layer (RFC 9728) clients depend on. Reading the actual spec, not just docs, finds
these.

### Bug 7: FastMCP's DNS-rebinding guard rejected our domain
**Symptom:** OAuth flow completed perfectly, JWT issued, but `POST /mcp` -> **421
Misdirected Request** with `WARNING Invalid Host header:
fitness-tracker-0ciu.onrender.com`.
**Cause:** FastMCP ships `TransportSecurityMiddleware` to block DNS-rebinding attacks.
The default `allowed_hosts` is just `localhost` / `127.0.0.1`. Our Render hostname
wasn't on the list.
**Fix:** Pass `transport_security=TransportSecuritySettings(allowed_hosts=[...])`
derived from `PUBLIC_BASE_URL`.
**Lesson:** Security middleware that's safe by default in dev is exactly what you'll
forget to configure for prod. The fact that it took the full happy-path through OAuth
before failing made this hard to spot — every other endpoint worked.

---

## 8. Code organization (file-by-file)

```
src/
├── app.py             <- top-level ASGI dispatcher (Bug 2 + Bug 3 fix lives here)
├── mcp_server.py      <- FastMCP construction, transport security (Bug 7 fix)
├── config.py          <- pydantic Settings from env
├── context.py         <- ContextVar for the auth'd user
├── auth/
│   ├── oauth.py       <- OAuth provider: metadata, register, authorize, token
│   ├── middleware.py  <- Bearer middleware (sets ContextVar, includes RFC 9728 link)
│   ├── tokens.py      <- issue + verify HS256 JWT, opaque refresh tokens
│   └── passwords.py   <- native bcrypt (Bug 4 fix)
├── db/client.py       <- Supabase singleton with @lru_cache
└── tools/
    ├── helpers.py     <- user/timezone, local-day boundaries, macro sums
    ├── nutrition.py   <- 19 tools (meals, water, goals, trends, patterns)
    └── fitness.py     <- 26 tools (workouts, cardio, steps, PRs, streaks)

scripts/
├── smoke.py           <- offline ASGI test (no Supabase) — runs in CI
└── e2e.py             <- real OAuth + MCP round-trip against a live server

supabase/schema.sql        <- all tables, RLS policies, seeded exercise catalog
.github/workflows/ci.yml   <- lint + smoke + Docker build on push
Dockerfile, render.yaml, requirements.txt, README.md
```

---

## 9. Likely interview questions and sample answers

**Q: Why MCP instead of just an HTTP API?**
> The point is conversational access. With an MCP server, Claude introspects the tool
> list at runtime and the model decides when to call which tool from natural language.
> I could have built a REST API, but then I'd need a separate UI or chatbot frontend.
> MCP turns Claude itself into the UI.

**Q: Walk me through your auth.**
> OAuth 2.0 authorization code with PKCE, plus dynamic client registration so Claude
> can self-register. On signup we bcrypt the password into Supabase. The auth endpoint
> issues an HS256 JWT (1-hour TTL) plus an opaque refresh token (30 days, DB-stored so
> it's revocable). The MCP endpoint has a thin ASGI middleware that pulls the bearer,
> verifies the JWT, sets a ContextVar, and hands off to FastMCP. Tool functions read
> the user from the ContextVar.

**Q: Why JWT for access and opaque for refresh?**
> Access tokens are validated on every tool call. JWT means no DB hit per request.
> Refresh tokens are validated rarely, but you need the ability to revoke them — so
> opaque + DB-stored. Standard hybrid pattern.

**Q: Why Supabase if you bypass it with the service-role key?**
> Two reasons. One, Postgres in five minutes with managed backups, dashboard, free
> tier. Two, RLS as defense in depth — even though server code filters by `user_id`,
> RLS guarantees the database refuses to return another user's row if my filter is
> wrong. It's belt-and-suspenders.

**Q: How do you handle timezones?**
> Each user has an IANA timezone in `app_users`. All timestamps in the DB are UTC.
> When the LLM asks for "today's meals", I compute local-day bounds in the user's
> timezone, convert to UTC, and query. So "today" means today *for them*, not for the
> server.

**Q: Tell me about a hard bug.**
> *(use Bug 7 — the host header / 421 — it's the most "real production" of the lot.)*
> The OAuth flow worked end-to-end, tokens were issued, but the very first MCP call
> after auth returned 421 Misdirected Request. The Render logs showed `Invalid Host
> header`. FastMCP has built-in DNS-rebinding protection that whitelists localhost by
> default — I had to configure it explicitly with my Render hostname. The painful
> part was that it failed *after* the entire happy-path through OAuth, so initial
> debugging suspected the token, then the JWT audience, then PKCE — until I read the
> WARNING line in the logs.

**Q: How would you scale this?**
> A few angles. Render's free tier sleeps after 15 min — first thing is upgrading or
> moving to Fly.io. The JWT verification is already stateless. Bottlenecks would be
> Supabase row counts on `workout_sets` for analytics — I'd add a materialized view
> for PR rollups. The trend queries today scan the table; partitioning by month or
> pre-aggregating into a daily summary table would scale further. For multi-region,
> the auth state in Supabase would need a global Postgres or replicated read replicas.

**Q: What would you do differently?**
> Three things. (1) I'd write the OAuth provider as a separate library — it's reusable
> for any future MCP server. (2) The exercise catalog is seeded into Postgres; for a
> richer product I'd source it from an open dataset like exercemus or wger. (3) I'd
> add a memory/context tool — let the user say "remember I have a left-knee injury"
> and have Claude pull that into context for future workouts.

**Q: What's the security model?**
> Three layers. (1) bcrypt-hashed passwords with a 72-byte clamp. (2) HS256 JWTs signed
> with a 48-byte secret kept in env vars, never in code or git. (3) Defense-in-depth
> RLS in Postgres so a bug in app code can't leak across users. PKCE on the
> authorization code prevents code interception in the browser redirect. Refresh
> tokens are revocable. Sessions cookies are HttpOnly + Secure + SameSite=Lax. The
> biggest gap honestly is rate limiting — `/oauth/authorize` is unprotected against
> brute force. For a real product I'd add per-IP throttling.

---

## 10. Numbers you can quote on your resume

- **45 MCP tools** across nutrition + fitness
- **14 database tables** with row-level-security policies
- **7 production bugs** debugged from logs to fix
- **5 OAuth/MCP RFCs** correctly implemented end-to-end (6749, 7591, 7636, 8414, 9728)
- **<200 ms** typical tool-call latency once warm
- **One Docker image, one Render web service** — full stack on free tier

---

# Part II — File-by-file reading guide

For each file: what to look for, concepts to learn deeply, specs/RFCs to read,
tools to install and try, and a small practice exercise to lock the knowledge in.

Labels used:
- **Concepts** — ideas to understand deeply (search-engine queries / book chapters)
- **Specs/RFCs** — the actual documents that define the standards
- **Tools** — software you should install and play with
- **Practice** — a small exercise to lock the concept in

---

## Phase 1 — The map (15 minutes)

### 1. `README.md`

**What to look for:** elevator pitch + user flow.

**Concepts:**
- Model Context Protocol (MCP) — what problem it solves
- Streamable HTTP transport (POST request -> JSON or SSE response)
- The "remote MCP server" pattern (vs local stdio MCP)

**Specs:** [modelcontextprotocol.io spec](https://modelcontextprotocol.io/specification)

**Tools:**
- `mcp-inspector` — official UI to poke an MCP server
- `mcp-cli` (community) — CLI client

**Practice:** describe the project in 3 sentences without using the words "MCP" or "OAuth."

---

### 2. `supabase/schema.sql` <- single most important file

**What to look for:**
- `app_users` has its own password_hash (we don't use Supabase Auth)
- Three OAuth state tables
- `workouts` has children in `workout_sets`
- `workout_sets.exercise_name` is denormalized alongside `exercise_id`
- `step_logs` uses `(user_id, log_date)` as composite PK
- RLS policy block + idempotent `do $$ begin ... end $$`
- Seeded global exercise catalog (28 rows of `null` user_id = global)

**Concepts:**
- **Relational modeling** — 1:1, 1:N, N:M relationships
- **Normalization vs denormalization** — why we denormalized `exercise_name`
- **Composite primary keys** (`step_logs`)
- **Foreign-key cascades** (`ON DELETE CASCADE` ripple)
- **Row-Level Security (RLS)** — `using` vs `with check`, `auth.uid()`
- **Indexes** — B-tree, partial, composite (e.g. `(user_id, consumed_at desc)`)
- **PostgreSQL extensions** — `pgcrypto`, `uuid-ossp`
- **Idempotent DDL** — `if not exists`, the `do $$ begin ... exception when duplicate_object then null; end $$;` trick
- **Migrations vs raw schema** — how teams evolve schemas (Alembic, Flyway)

**Tools:**
- `psql` — official Postgres CLI
- DBeaver / TablePlus / pgAdmin — GUIs
- `EXPLAIN ANALYZE` — query plans
- `pgcli` — psql with autocomplete

**Practice:** sketch the ER diagram on paper from memory. Then add a `meal_tags`
table (many-to-many) without breaking RLS.

---

### 3. `requirements.txt`

**Concepts:**
- **Python packaging** — `pip`, `pyproject.toml`, lock files
- **PEP 508 version specifiers** — `>=`, `==`, `~=`, extras like `[bcrypt]`
- **Transitive dependency management** — why bcrypt 5.x broke us (Bug 4)
- **Reproducible builds** — why teams use `requirements.lock` or `uv.lock`

**Tools:**
- `pip-tools` (`pip-compile`)
- `uv` — extremely fast modern Python package manager (Astral)
- `poetry`
- `pipdeptree` — visualize transitive deps

**Practice:** convert this project to a fully-locked `uv.lock` file with
`uv pip compile requirements.txt`.

---

## Phase 2 — Boot mechanics (5 minutes)

### 4. `src/config.py`

**Concepts:**
- **12-factor app methodology** — config in env, not code
- **`functools.lru_cache`** as a singleton pattern (`@lru_cache` on no-arg function = "compute once")
- **Pydantic** for runtime validation
- **The difference between dev secrets, staging secrets, prod secrets**

**Tools:**
- `pydantic-settings` — Pydantic's BaseSettings (handles env automatically)
- `python-dotenv`
- `direnv` — project-scoped env vars on `cd`
- HashiCorp Vault, AWS Secrets Manager, Doppler — real secrets backends

**Practice:** convert `Settings` to `pydantic_settings.BaseSettings` so it reads env
vars natively.

---

### 5. `src/db/client.py`

**Concepts:**
- Service-role vs anon keys in Supabase
- **Defense in depth** — why we keep RLS on even though service-role bypasses it
- **Singleton vs request-scoped clients** — when each is appropriate
- **Connection pooling** — how supabase-py handles it under the hood (PostgREST is
  stateless HTTP, so no real pool — different from psycopg2)

**Practice:** add a small `health_check()` function that pings Supabase and returns
latency.

---

### 6. `src/context.py` <- tiny, central

**Concepts:**
- **`contextvars`** — Python's per-task / per-request state (PEP 567)
- **Why threadlocals don't work for asyncio** — one event loop runs many tasks;
  thread-local would leak across them
- **Structured concurrency** — Trio's idea, partially in `asyncio.TaskGroup`
- **Implicit vs explicit dependency passing** — ContextVar = implicit; function arg =
  explicit. Tradeoffs.

**Specs:** [PEP 567 — Context Variables](https://peps.python.org/pep-0567/)

**Practice:** reproduce the pattern in a 30-line script: a middleware sets
`current_user`, two concurrent `asyncio.gather` calls each see their own value.

---

## Phase 3 — Request flow (25 minutes)

### 7. `src/app.py` — entry point

**What to look for:** the `dispatch()` function. Trace these branches:
- `lifespan` scope -> goes through `inner_starlette` (so FastMCP's session manager initializes)
- path starts with `/mcp` -> strip prefix, hand to `mcp_handler`
- everything else -> through `inner_starlette`

**Concepts:**
- **ASGI specification** — the protocol uvicorn <-> Starlette <-> FastMCP all speak
- **Three ASGI scope types**: `http`, `websocket`, `lifespan`
- **Why ASGI replaced WSGI** — async support, websockets, server push
- **Lifespan events** — startup/shutdown signaling
- **HTTP redirects and the Authorization header** — many clients drop it on 3xx
  (RFC 7235 §2.1, but client behavior varies)
- **Reverse proxy headers** — `X-Forwarded-For`, `X-Forwarded-Proto`

**Specs:** [ASGI spec](https://asgi.readthedocs.io/en/latest/specs/main.html)

**Tools:**
- `uvicorn` (we use), `hypercorn`, `daphne` — ASGI servers
- `gunicorn` + uvicorn workers — production combo

**Practice:** add a `print()` at the top of `dispatch()` that logs
`scope["path"] + scope["method"]`. Run smoke and watch the trace.

---

### 8. `src/auth/middleware.py` — bearer gate

**What to look for:** `BearerAuthMiddleware.__call__`. Trace the four-step gate:
1. Pull `Authorization` header
2. If no `Bearer ...` -> 401 with the RFC 9728 `resource_metadata` link
3. If invalid token -> 401
4. If valid -> set `current_user` ContextVar, run inner app, reset on the way out

Pay attention to the `try/finally` around `current_user.reset(tok)` — that's how
concurrent requests stay isolated.

**Concepts:**
- **Functional vs class-based ASGI middleware**
- **HTTP status codes for auth** — 401 (no/bad credentials) vs 403 (authenticated but forbidden)
- **`WWW-Authenticate` header structure** — challenge schemes, `realm`, parameters
- **The `resource_metadata` link parameter** (RFC 9728 §5.3)
- **Sending raw ASGI responses** vs using Starlette `Response`

**Specs:** [RFC 6750 — Bearer Token Usage](https://www.rfc-editor.org/rfc/rfc6750),
[RFC 9728 §5.3](https://www.rfc-editor.org/rfc/rfc9728)

**Practice:** add a second middleware in front of bearer that logs request duration.
Get the order right.

---

### 9. `src/auth/tokens.py` — JWT

**What to look for:**
- `issue_access_token` — note `aud=f"{issuer}/mcp"` (RFC 8707, Resource Indicators)
- `verify_access_token` — note `verify_aud: False` (we don't enforce, just record)
- `new_opaque_token` — refresh tokens use this; they're not JWTs

**Concepts:**
- **JWT anatomy**: `header.payload.signature` (base64url) — open `jwt.io` and paste a token
- **JWT claims**: `iss`, `sub`, `aud`, `exp`, `iat`, `nbf`, `jti`
- **Symmetric (HS256) vs asymmetric (RS256/ES256)** — when to choose each
- **JWT vs opaque tokens** — stateless verification vs revocability
- **Token rotation** — issue new refresh on every use, revoke the old
- **Why we set `aud = <issuer>/mcp`** — RFC 8707 (Resource Indicators)
- **JWT pitfalls** — the famous "alg: none" vulnerability, key confusion, no built-in revocation

**Specs:**
- [RFC 7519 (JWT)](https://www.rfc-editor.org/rfc/rfc7519)
- [RFC 7515 (JWS)](https://www.rfc-editor.org/rfc/rfc7515)
- [RFC 8707 (Resource Indicators)](https://www.rfc-editor.org/rfc/rfc8707)

**Tools:**
- [jwt.io](https://jwt.io) — paste, decode, debug
- `python-jose`, `PyJWT`, `authlib` — Python JWT libraries
- `step` (smallstep) — CLI that does JWT signing + JWKS

**Practice:** mint a token by hand with `python-jose`, decode it on jwt.io, change one
bit of the signature, re-decode — confirm it fails verification.

---

### 10. `src/auth/passwords.py` — bcrypt

**Concepts:**
- **Password hashing**: bcrypt, Argon2, scrypt, PBKDF2 — what each is good for
- **Cost factor / work factor** — why bcrypt rounds matter, how to tune
- **Salt** — why per-password salting prevents rainbow tables
- **Constant-time comparison** — timing attacks, why `hmac.compare_digest` exists
- **Why never SHA256(password) alone** — too fast, brute-forceable
- **bcrypt's 72-byte hard ceiling** — why, what it means

**Tools:**
- `argon2-cffi` — recommended over bcrypt for new systems
- OWASP Password Storage Cheat Sheet

**Practice:** write a benchmark: hash 100 passwords with bcrypt at cost 10, then 12,
then 14 — measure how time scales.

---

## Phase 4 — OAuth (30 minutes — densest)

### 11. `src/auth/oauth.py`

This is the biggest file. **Read it in this order, not top-to-bottom:**

1. **The route table at the bottom** (`oauth_routes = [...]`) — see what endpoints exist.
2. **`metadata()`** — RFC 8414 discovery doc.
3. **`protected_resource_metadata()`** — RFC 9728. Note `resource = "{issuer}/mcp"`.
4. **`register_client()`** — RFC 7591 dynamic client registration.
5. **`_LOGIN_HTML`** + **`_render_login()`** — single inline HTML template, no Jinja.
6. **`authorize()`** <- the heart. Read in two halves:
   - GET handling: validate params -> if user has a session cookie, issue code immediately;
     otherwise show login page.
   - POST handling: signup vs login mode -> password hash or verify -> issue auth code
     -> 302 back to Claude with `?code=...&state=...`.
7. **`_issue_code()`** — saves the code to `oauth_auth_codes` with `code_challenge` for PKCE.
8. **`token()`** — exchange flow. Two grant types:
   - `authorization_code`: lookup code, verify PKCE, mark consumed, issue token pair.
   - `refresh_token`: lookup refresh, revoke old, issue new pair (rotation).
9. **`_issue_token_pair()`** — JWT + opaque refresh, with the refresh stored in DB.
10. **`_verify_pkce()`** — base64url(sha256(verifier)) == challenge.

**Concepts:**

*Core OAuth 2.0:*
- The four roles: **Resource Owner, Client, Authorization Server, Resource Server**
- The four grant types: **authorization_code** (we use), client_credentials, password
  (deprecated), implicit (deprecated)
- Public vs confidential clients
- The `state` parameter — CSRF defense in OAuth
- Refresh token rotation, family detection (advanced)

*PKCE (Proof Key for Code Exchange):*
- The threat model — code interception in browser redirects
- `S256` vs `plain` (`plain` is for legacy clients only — never trust in prod)
- The flow: `verifier` -> `challenge = base64url(sha256(verifier))` -> only `verifier`
  exchanges the code

*Discovery:*
- RFC 8414 — OAuth Authorization Server Metadata
- RFC 9728 — OAuth Protected Resource Metadata
- OpenID Connect Discovery (`.well-known/openid-configuration`) — superset of RFC 8414

*Sessions:*
- Signed cookies via `itsdangerous`
- Cookie flags: `HttpOnly`, `Secure`, `SameSite=Lax|Strict|None`

**Specs (in order of importance):**
1. [RFC 6749 — OAuth 2.0 framework](https://www.rfc-editor.org/rfc/rfc6749)
2. [RFC 7636 — PKCE](https://www.rfc-editor.org/rfc/rfc7636)
3. [RFC 8414 — Authorization Server Metadata](https://www.rfc-editor.org/rfc/rfc8414)
4. [RFC 7591 — Dynamic Client Registration](https://www.rfc-editor.org/rfc/rfc7591)
5. [RFC 9728 — Protected Resource Metadata](https://www.rfc-editor.org/rfc/rfc9728)
6. [OAuth 2.1 draft](https://datatracker.ietf.org/doc/draft-ietf-oauth-v2-1/) — what
   the next version cleans up

**Tools:**
- [oauth.tools](https://oauth.tools) — visual flow simulator
- [Authlib](https://authlib.org/) — production-grade Python OAuth library (study its source)
- `httpie`, Postman — to manually walk the flow
- Auth0 / Keycloak / Ory Hydra — real OAuth servers (for comparison)

**Practice:**
1. Walk the e2e.py script line by line and identify which RFC each step implements.
2. Try to break PKCE: capture the code mid-flight, send it to `/oauth/token` without
   the verifier — confirm it fails.
3. Read Authlib's authorization server module — see how a "real" library handles edge
   cases we skipped (token introspection, token revocation, JWE-wrapped tokens).

---

## Phase 5 — MCP server (20 minutes)

### 12. `src/mcp_server.py`

**What to look for:**
- `FastMCP(...)` construction with `instructions=` (the system-prompt the LLM sees)
- `streamable_http_path="/"` — explained by the `app.py` mount comment
- `transport_security=` — the fix for Bug 7 (DNS-rebinding allow-list)
- The two `register()` calls — that's where 45 tool decorators get attached

**Concepts:**
- **JSON-RPC 2.0** (the wire protocol underneath MCP) — methods, params, id, batch requests
- **MCP methods**: `initialize`, `tools/list`, `tools/call`, `resources/list`,
  `resources/read`, `prompts/list`
- **Streamable HTTP transport** — POST returns either JSON or `text/event-stream`
- **Stateful vs stateless MCP** — we use `stateless_http=True`
- **DNS rebinding attack** — the threat the host whitelist defends against
- **Tool descriptions = prompt engineering** — what the LLM sees decides what gets called

**Specs:**
- [MCP base spec](https://modelcontextprotocol.io/specification)
- [JSON-RPC 2.0](https://www.jsonrpc.org/specification)

**Tools:**
- MCP Inspector
- `mcp` Python SDK source — read `mcp/server/fastmcp/server.py`

**Practice:** add a single new tool `roll_dice(sides: int) -> int` and confirm it
shows up in MCP Inspector.

---

### 13. `src/tools/helpers.py`

**What to look for:**
- `user_and_tz()` — every tool that handles dates calls this
- `local_day_bounds(tz_name, day)` — converts "today" to UTC range
- `iso()`, `parse_date()`, `sum_macros()` — small utilities

**Concepts:**
- **IANA timezone database** (`Asia/Kolkata`, `Europe/London`)
- **Why store UTC and convert on read** — DST changes, server moves, user-relocation
- **`pytz` vs `zoneinfo`** (Python 3.9+) — `zoneinfo` is the modern stdlib version;
  pytz is older but ubiquitous
- **Half-open intervals** (`>=` start, `<` end) — why `local_day_bounds` returns
  24h-range, not "midnight to 23:59:59"
- **PostgreSQL `timestamptz`** — what's actually stored

**Tools:**
- [time.is](https://time.is)
- `dateutil` for fuzzy parsing
- The `zoneinfo` stdlib module

**Practice:** rewrite `local_day_bounds` using `zoneinfo` instead of `pytz`; confirm
tests still pass.

---

### 14. `src/tools/nutrition.py`

**What to look for** (don't read all 19 — read these and skim the rest):
- `log_meal` — simplest tool
- `update_meal` — sparse-update pattern
- `get_meals_today` — uses `user_and_tz` + `local_day_bounds`
- `get_meals_by_date_range` — per-day buckets
- `get_goal_progress` — joins meals + water + goals
- `get_trends` — compare-windows pattern (mirrored in fitness)
- `set_timezone` — IANA validation

**Concepts:**
- **PostgREST's filter chain** — `.eq().gte().lt().order().execute()`
- **JSON Schema generation from Python type hints** (FastMCP introspects signatures)
- **Sparse update pattern** — only send fields the user provided, don't overwrite with NULL
- **`Literal[...]`** for enum-like params

**Tools:**
- [PostgREST docs](https://postgrest.org)
- `supabase-py` source

**Practice:** add a `delete_meals_by_date(day)` tool that bulk-deletes all meals on a day.

---

### 15. `src/tools/fitness.py`

**What to look for:**
- `epley_1rm` (top of file) — the `weight * (1 + reps/30)` formula
- `log_workout` — handles a list of sets in one call, auto-numbering per exercise
- `get_exercise_history` — heaviest set + best e1RM lookup
- `get_personal_records` — aggregates across all exercises
- `get_workout_trends` — recent vs prior window
- `get_workout_patterns` — joins to `exercises` for muscle groups
- `get_consistency_streak` — walks backward from today
- `get_fitness_summary` — rolling N-day rollup

**Concepts:**
- **Domain modeling** for fitness — set, rep, RPE (Rate of Perceived Exertion), warmup
- **Estimated 1RM formulas** — Epley (`w * (1 + r/30)`), Brzycki (`w * 36/(37-r)`), Lombardi
- **Progressive overload** — the underlying training principle
- **Window comparison analytics** — recent N days vs prior N days
- **Streaks / cohort analysis** — walking back from today
- **Aggregation by category** — building muscle-group volume maps
- **Denormalization payoff** — why putting `exercise_name` in `workout_sets` makes PR
  queries one table scan instead of a join

**Practice:**
- Implement Brzycki alongside Epley and let the user pick.
- Add a `get_volume_by_muscle(week)` tool that bins volume per primary muscle.

---

## Phase 6 — Tests (15 minutes)

### 16. `scripts/smoke.py`

**What to look for:**
- `_LifespanRunner` — manually drives the ASGI lifespan since
  `httpx.ASGITransport` doesn't
- `EXPECTED_TOOLS` — a flat list of all 45 tool names
- The 6 in-order assertions — mirrors the production HTTP probe set

**Concepts:**
- **In-process testing** — no live network, fast feedback
- **`httpx.ASGITransport`** — speak ASGI without a real socket
- **Manual lifespan driving** — what `_LifespanRunner` is doing
- **Test pyramid** — unit -> integration -> e2e

**Tools:**
- `pytest` + `pytest-asyncio`
- `asgi-lifespan` (the proper library that does what `_LifespanRunner` does)

**Practice:** convert `smoke.py` into proper pytest cases (`test_oauth_metadata`,
`test_mcp_rejects_no_auth`, etc.).

---

### 17. `scripts/e2e.py` <- the canonical reference for the OAuth flow

**What to look for:** trace the full sequence:
1. GET metadata
2. POST `/oauth/register`
3. PKCE pair generation (`_pkce_pair`)
4. POST `/oauth/authorize` (signup or login mode)
5. Parse the redirect -> extract `code`
6. POST `/oauth/token` with `code_verifier`
7. POST `/mcp` with `initialize`, then `tools/list`, then `tools/call`

**Concepts:**
- **End-to-end testing** — the whole stack, real Supabase
- **PKCE generation in client code**
- **Server-Sent Events parsing** (`data:` lines)
- **Cookie-less HTTP clients** — sessions still work via response cookies

**Tools:**
- `playwright` — for any browser-based step in OAuth (some flows need real browsers)
- `requests-mock` / `respx` — to mock HTTP layer

**Practice:** add a step at the end that calls `log_meal` then `get_meals_today` and
asserts the meal appears.

---

### 18. `.github/workflows/ci.yml`

**What to look for:** lint + smoke + Docker build per push. No live e2e in CI (no
Supabase secrets).

**Concepts:**
- **Continuous Integration** — what failure should block, what shouldn't
- **GitHub Actions YAML** — `jobs`, `steps`, `needs`, `uses` (action references)
- **Caching strategies** — pip cache, Docker layer cache via `type=gha`
- **Job dependencies** (`needs:`) — fail-fast vs continue-on-error
- **Secrets in CI** — never echo, never commit

**Tools:**
- `act` (nektos) — run GitHub Actions locally
- `actionlint` — lint your workflow files
- CircleCI / GitLab CI / Buildkite — alternatives

**Practice:** add a step that runs `mypy` on `src/`. Watch what breaks. Fix it.

---

## Phase 7 — Operations (skim, 5 minutes)

### 19. `Dockerfile`

**Concepts:**
- **Layer caching** — instruction order matters (`COPY requirements.txt` before `COPY src/`)
- **Slim vs full base images** — image-size tradeoff
- **Multi-stage builds** — compile in one stage, copy artifacts to a smaller runtime stage
- **Reproducibility** — pinned base image SHA vs floating tag
- **Reverse-proxy header trust** — `--proxy-headers` and why
- **Distroless / chainguard images** — security-hardened bases

**Tools:**
- `dive` — interactive layer explorer
- `hadolint` — Dockerfile linter
- `trivy` — vuln scanner for images
- `docker buildx` — buildkit features

**Practice:** convert this to a multi-stage Dockerfile that builds a tiny runtime image.

---

### 20. `render.yaml`

**Concepts:**
- **Infrastructure as Code (IaC)** — declarative deployments
- **PaaS vs IaaS vs serverless** — Render is PaaS (managed runtime + autoprovisioned infra)
- **Free-tier tradeoffs** — cold starts, ephemeral disk, shared CPU
- **Health checks** and **readiness probes**
- **Auto-deploy on git push** — webhook-driven

**Tools:**
- Render alternatives: Fly.io, Railway, Koyeb, Cloud Run
- For real IaC: Terraform, Pulumi, AWS CDK
- Kubernetes — when PaaS isn't enough

**Practice:** write the equivalent `fly.toml` for Fly.io.

---

### 21. `.env.example`

**Concepts:**
- **Secret management hierarchy** — env vars (basic) -> secret managers (real) -> KMS (hardware-backed)
- **Why `.env` is gitignored**
- **Per-environment configs** — dev/staging/prod
- **Secret rotation policies**

**Tools:**
- AWS Secrets Manager / GCP Secret Manager / HashiCorp Vault
- Doppler / 1Password Secrets Automation
- `sops` — encrypted files in git, decrypt at runtime

---

# Part III — Cross-cutting curriculum

These don't belong to one file but show up everywhere. Master these and you'll level
up across any backend role.

## A. System design fundamentals
- **CAP theorem**, eventual consistency
- **Idempotency** — why API design demands it
- **Caching strategies** — write-through, write-behind, TTL, cache invalidation
- **Pagination patterns** — offset vs cursor
- **Rate limiting** — token bucket, leaky bucket
- **Read [Designing Data-Intensive Applications](https://dataintensive.net/)** by
  Martin Kleppmann (the canonical book)

## B. Web security
- **OWASP Top 10** — read it twice
- **CSRF, XSS, SSRF, IDOR** — what each is, how to defend
- **Same-origin policy, CORS** — when to enable, when never
- **Content-Security-Policy** headers
- **Mozilla Observatory** — automated security scoring
- **Practice:** run [securityheaders.com](https://securityheaders.com) on your Render
  URL. Hit A+.

## C. Async Python
- **The event loop, coroutines, tasks** — what `asyncio.run()` actually does
- **`async with`, `async for`** — async context/iterators
- **Cancellation** — how `CancelledError` propagates
- **Structured concurrency** — `asyncio.TaskGroup` (3.11+)
- **`anyio`** — uniform abstraction over asyncio + Trio
- Read Lukasz Langa's PyCon talks on asyncio internals

## D. PostgreSQL deeper dive
- **Transactions, isolation levels** — read committed vs serializable
- **Indexes** — when each type wins (B-tree, GIN, GiST, BRIN)
- **`EXPLAIN ANALYZE`** — read query plans
- **Vacuum and bloat**
- **JSON columns** (`jsonb`) — when to use, when to normalize
- **CTEs and window functions** — `OVER (PARTITION BY ...)` for analytics

## E. Observability
- **The three pillars**: logs, metrics, traces
- **Structured logging** (JSON logs, not `print`)
- **OpenTelemetry** — vendor-neutral instrumentation
- **Prometheus + Grafana** — the open-source metrics stack
- **Sentry** — error tracking
- **Practice:** wire `structlog` into the project. Replace every `print` with a
  structured logger.

## F. Testing strategy
- **Test pyramid** — many unit, fewer integration, few e2e
- **Property-based testing** with Hypothesis
- **Mutation testing** with `mutmut` — does your test suite actually catch bugs?
- **Contract testing** — Pact for API consumers/providers

## G. Networking + HTTP
- **HTTP/1.1 vs HTTP/2 vs HTTP/3** — head-of-line blocking, multiplexing, QUIC
- **TLS handshake** — what actually happens, certificate chains
- **mTLS** — when both sides authenticate
- **DNS** — A, AAAA, CNAME, TXT, MX
- Read **High Performance Browser Networking** by Ilya Grigorik (free online)

## H. Identity & access management beyond OAuth
- **OpenID Connect** — OAuth + identity layer
- **SAML** — enterprise SSO
- **WebAuthn / passkeys** — passwordless auth
- **Authorization models** — RBAC, ABAC, ReBAC (Google Zanzibar / SpiceDB)

---

# Part IV — Suggested 4-week study plan

| Week | Focus | Files | Side reading |
|---|---|---|---|
| 1 | Get the architecture and DB | Phase 1 + 2 + 3 | RFC 6749 (OAuth), DDIA Ch. 1-3 |
| 2 | OAuth deep dive | `oauth.py` + `tokens.py` + `e2e.py` | RFCs 7636, 8414, 9728. Read Authlib source. |
| 3 | MCP + tools | Phase 5 entirely | MCP spec, JSON-RPC 2.0 spec |
| 4 | Ops + tests + curriculum gaps | Phases 6 + 7 | OWASP Top 10, OpenTelemetry intro |

End of each week: write a 1-paragraph blog post summarizing what you learned. That's
how you turn "I read it" into "I understand it well enough to teach it" — which is the
level interviewers actually probe.

---

# Appendix A — Database deep dive (Supabase / Postgres)

This appendix is the field manual for the schema. Read this and you can answer any
question about the data model — column meanings, why a constraint exists, why an
index is shaped that way, what cascades on delete.

---

## A.1 Big picture (entity-relationship diagram)

> **For a high-quality, readable rendering**, open the pre-generated images:
> - [`docs/schema-overview.png`](docs/schema-overview.png) — color-coded big picture
> - [`docs/schema-auth.svg`](docs/schema-auth.svg) — OAuth/identity tables in detail
> - [`docs/schema-nutrition.svg`](docs/schema-nutrition.svg) — nutrition tables in detail
> - [`docs/schema-fitness.svg`](docs/schema-fitness.svg) — fitness tables in detail
>
> SVGs are vector — zoom in your browser to read every column.
>
> The Mermaid source below is also rendered natively by GitHub when viewing this file.

```mermaid
erDiagram
    app_users ||--o{ oauth_auth_codes      : "owns"
    app_users ||--o{ oauth_refresh_tokens  : "owns"
    app_users ||--o{ meals                 : "logs"
    app_users ||--o{ water_logs            : "logs"
    app_users ||--|| nutrition_goals       : "has 1"
    app_users ||--o{ exercises             : "owns custom"
    app_users ||--o{ workouts              : "performs"
    app_users ||--o{ workout_sets          : "performs"
    app_users ||--o{ cardio_sessions       : "logs"
    app_users ||--o{ step_logs             : "logs daily"
    app_users ||--o{ body_metrics          : "records"
    app_users ||--|| fitness_goals         : "has 1"

    oauth_clients ||--o{ oauth_auth_codes      : "issued for"
    oauth_clients ||--o{ oauth_refresh_tokens  : "issued for"

    workouts  ||--o{ workout_sets : "contains"
    exercises ||--o{ workout_sets : "logged as (nullable FK)"

    app_users {
        uuid id PK
        text email UK
        text password_hash
        text timezone
        timestamptz created_at
        timestamptz last_login_at
    }
    oauth_clients {
        text client_id PK
        text client_secret
        text client_name
        text_array redirect_uris
        text_array grant_types
        text token_endpoint_auth_method
        timestamptz created_at
    }
    oauth_auth_codes {
        text code PK
        text client_id FK
        uuid user_id FK
        text redirect_uri
        text scope
        text code_challenge
        text code_challenge_method
        timestamptz expires_at
        boolean consumed
        timestamptz created_at
    }
    oauth_refresh_tokens {
        text token PK
        text client_id FK
        uuid user_id FK
        text scope
        timestamptz expires_at
        boolean revoked
        timestamptz created_at
    }
    meals {
        uuid id PK
        uuid user_id FK
        text name
        text meal_type
        numeric calories
        numeric protein_g
        numeric carbs_g
        numeric fat_g
        numeric fiber_g
        numeric sugar_g
        numeric sodium_mg
        text notes
        timestamptz consumed_at
        timestamptz created_at
    }
    water_logs {
        uuid id PK
        uuid user_id FK
        integer amount_ml
        timestamptz consumed_at
        timestamptz created_at
    }
    nutrition_goals {
        uuid user_id PK_FK
        numeric calories
        numeric protein_g
        numeric carbs_g
        numeric fat_g
        numeric fiber_g
        integer water_ml
        timestamptz updated_at
    }
    exercises {
        uuid id PK
        uuid user_id FK_nullable
        text name
        text category
        text primary_muscle
        text equipment
        boolean is_custom
        timestamptz created_at
    }
    workouts {
        uuid id PK
        uuid user_id FK
        text name
        text notes
        timestamptz started_at
        timestamptz ended_at
        integer duration_min
        timestamptz created_at
    }
    workout_sets {
        uuid id PK
        uuid workout_id FK
        uuid user_id FK
        text exercise_name "denormalized"
        uuid exercise_id FK_nullable
        integer set_number
        integer reps
        numeric weight_kg
        numeric rpe
        boolean is_warmup
        text notes
        timestamptz created_at
    }
    cardio_sessions {
        uuid id PK
        uuid user_id FK
        text activity
        numeric duration_min
        numeric distance_km
        numeric calories
        integer avg_heart_rate
        integer max_heart_rate
        text notes
        timestamptz performed_at
        timestamptz created_at
    }
    step_logs {
        uuid user_id PK_FK
        date log_date PK
        integer steps
        numeric distance_km
        numeric calories
        timestamptz updated_at
    }
    body_metrics {
        uuid id PK
        uuid user_id FK
        timestamptz measured_at
        numeric weight_kg
        numeric body_fat_pct
        numeric waist_cm
        numeric chest_cm
        numeric arm_cm
        numeric thigh_cm
        integer resting_hr
        text notes
        timestamptz created_at
    }
    fitness_goals {
        uuid user_id PK_FK
        integer daily_steps
        integer weekly_workouts
        integer weekly_cardio_min
        numeric weekly_volume_kg
        numeric target_weight_kg
        numeric target_body_fat_pct
        timestamptz updated_at
    }
```

### ASCII fallback (if Mermaid doesn't render in your viewer)

```
                          +-------------------+
                          |    app_users      |   <-- the root entity
                          |  id (uuid) PK     |       everything cascades from here
                          |  email UNIQUE     |
                          |  password_hash    |
                          |  timezone (IANA)  |
                          +---------+---------+
                                    |
        +---------------------------+---------------------------+
        |  IDENTITY / OAUTH STATE                                |
        |  +-----------------+   +--------------------+          |
        |  |  oauth_clients  |-->|  oauth_auth_codes  |          |
        |  |  client_id PK   |   |  code PK           |          |
        |  +-----------------+   |  client_id FK      |--+       |
        |          |             |  user_id FK        |  |       |
        |          +------------>|  code_challenge    |  |       |
        |                        |  expires_at        |  |       |
        |                        |  consumed          |  |       |
        |                        +--------------------+  |       |
        |                        +------------------------+      |
        |                        | oauth_refresh_tokens   |      |
        |                        | token PK               |      |
        |                        | client_id FK, user FK  |      |
        |                        | revoked, expires_at    |      |
        |                        +------------------------+      |
        +--------------------------------------------------------+
                                    |
        +---------------------------+---------------------------+
        |  NUTRITION                                            |
        |  meals (1:N)        water_logs (1:N)                  |
        |  nutrition_goals (1:1, PK = user_id)                  |
        +-------------------------------------------------------+
                                    |
        +---------------------------+---------------------------+
        |  FITNESS                                              |
        |                                                       |
        |  exercises (1:N, OR global if user_id IS NULL)        |
        |        ^                                              |
        |        |  exercise_id (nullable, ON DELETE SET NULL)  |
        |        |                                              |
        |  workouts (1:N) ---contains---> workout_sets (1:N)    |
        |                                                       |
        |  cardio_sessions (1:N)                                |
        |  step_logs (composite PK: user_id + log_date)         |
        |  body_metrics (1:N)                                   |
        |  fitness_goals (1:1, PK = user_id)                    |
        +-------------------------------------------------------+
```

### Cardinality cheat sheet

| Relationship | Type | Notes |
|---|---|---|
| `app_users` → `meals` | 1:N | one user, many meal rows |
| `app_users` → `nutrition_goals` | 1:1 | `user_id` is the PK; row is created on signup |
| `app_users` → `fitness_goals` | 1:1 | same pattern |
| `app_users` → `step_logs` | 1:N (one per day) | composite PK enforces one row per user-day |
| `app_users` → `exercises` | 1:N | only for *custom* exercises; `user_id IS NULL` rows are global |
| `oauth_clients` → `oauth_auth_codes` | 1:N | a single client issues many codes over its lifetime |
| `workouts` → `workout_sets` | 1:N | parent / child within one session |
| `exercises` → `workout_sets` | 1:N (nullable) | `exercise_id` may be `NULL` if the catalog row was deleted (see `ON DELETE SET NULL`) |

---

## A.2 The three logical clusters

The 14 tables fall into three groups:

### Cluster 1 — Identity (1 table)
- **`app_users`** — every user account. Email + bcrypt hash + IANA timezone.
- This is the **root entity**. Every other user-owned row has a FK to it, with
  `ON DELETE CASCADE`. Deleting an account wipes everything.

### Cluster 2 — OAuth provider state (3 tables)
- **`oauth_clients`** — registered OAuth client apps (Claude.ai installs).
- **`oauth_auth_codes`** — short-lived (10-minute) one-time codes from the authorize step.
- **`oauth_refresh_tokens`** — opaque refresh tokens, revocable, 30-day TTL.
- Notice that `oauth_clients` is **not** owned by any single user — clients are
  registered globally. The `user_id` on the code/token rows links the *issuance event*
  (which user signed in to consent) to the underlying account.

### Cluster 3 — User-data tables (10 tables)
Split into two domains:

**Nutrition (3):**
- `meals` — every meal logged
- `water_logs` — every water entry
- `nutrition_goals` — one row per user with target macros / water

**Fitness (7):**
- `exercises` — global catalog + user customs
- `workouts` — sessions
- `workout_sets` — sets within sessions (children of `workouts`)
- `cardio_sessions` — runs, rides, swims
- `step_logs` — one row per user per local date
- `body_metrics` — weight, body-fat, circumferences
- `fitness_goals` — one row per user with strength/cardio/step targets

---

## A.3 Table-by-table deep dive

For each table: purpose, every column with type and meaning, constraints, indexes,
RLS policy, and how the application code uses it.

### A.3.1 `app_users`

> The root entity. One row per registered account.

| Column | Type | Meaning |
|---|---|---|
| `id` | `uuid` PK, `default gen_random_uuid()` | surrogate key |
| `email` | `text` UNIQUE NOT NULL | login identifier |
| `password_hash` | `text` NOT NULL | bcrypt output (`$2b$...`) |
| `timezone` | `text` NOT NULL DEFAULT `'UTC'` | IANA name (e.g. `Asia/Kolkata`) |
| `created_at` | `timestamptz` NOT NULL DEFAULT `now()` | signup time |
| `last_login_at` | `timestamptz` | updated on each successful login |

**Indexes:**
- `app_users_email_idx` on `(lower(email))` — case-insensitive lookups during login.
  Without `lower()`, `WHERE email = 'Foo@bar.com'` would not match `'foo@bar.com'`
  via the index.

**RLS:** *not enabled*. `app_users` is only ever written by the OAuth provider code,
never directly via Supabase from a browser.

**Used in code:**
- `oauth.py::authorize` — signup INSERT, login SELECT
- `tools/helpers.py::user_and_tz` — read timezone on every dated tool call
- `tools/nutrition.py::set_timezone` — UPDATE `timezone`
- `tools/nutrition.py::delete_account` — DELETE; cascade clears everything

---

### A.3.2 `oauth_clients`

> Stores every OAuth client that has registered with us via RFC 7591 dynamic
> registration. Claude.ai registers once per user-install.

| Column | Type | Meaning |
|---|---|---|
| `client_id` | `text` PK | format: `c_<24 base64url chars>` |
| `client_secret` | `text` NULL | populated only for confidential clients (`token_endpoint_auth_method != 'none'`). For Claude (public client) it's NULL. |
| `client_name` | `text` | human-friendly name like "Claude" |
| `redirect_uris` | `text[]` NOT NULL | whitelist of allowed callback URIs |
| `grant_types` | `text[]` NOT NULL DEFAULT `['authorization_code','refresh_token']` | which OAuth grants this client may use |
| `token_endpoint_auth_method` | `text` NOT NULL DEFAULT `'none'` | RFC 7591 auth method |
| `created_at` | `timestamptz` NOT NULL DEFAULT `now()` | registration time |

**Note on `redirect_uris` as an array:** Postgres arrays are first-class. We use it
because RFC 7591 explicitly allows multiple URIs per client. Validation: the user-supplied
`redirect_uri` on `/oauth/authorize` must appear in this array exactly. Membership test
is `redirect_uri in (r.data.get("redirect_uris") or [])` in code.

**RLS:** *not enabled*. Globally writable only by the server (service role).

---

### A.3.3 `oauth_auth_codes`

> A short-lived authorization code linking a logged-in user to a pending token
> exchange. PKCE state lives here.

| Column | Type | Meaning |
|---|---|---|
| `code` | `text` PK | random 32-byte base64url string |
| `client_id` | `text` FK → `oauth_clients(client_id)` ON DELETE CASCADE | which client this code belongs to |
| `user_id` | `uuid` FK → `app_users(id)` ON DELETE CASCADE | which user authenticated |
| `redirect_uri` | `text` NOT NULL | recorded so token exchange can verify it matches |
| `scope` | `text` | OAuth scope, currently always `mcp` |
| `code_challenge` | `text` | PKCE challenge from the authorize request |
| `code_challenge_method` | `text` | `S256` or `plain` |
| `expires_at` | `timestamptz` NOT NULL | 10 minutes after creation |
| `consumed` | `boolean` NOT NULL DEFAULT `false` | flipped to true on token exchange — codes are single-use |
| `created_at` | `timestamptz` NOT NULL DEFAULT `now()` | |

**Why store `code_challenge` here?** The PKCE flow requires the auth server to remember
the *challenge* during the authorize step and verify the *verifier* later during token
exchange. Stateless verification (e.g. encoding the challenge in the code itself) is
possible but more complex.

**Lifecycle:** create on `/oauth/authorize` → consume on `/oauth/token`. Rows are kept
even after consumption (could be cleaned up by a periodic job, not currently done).

---

### A.3.4 `oauth_refresh_tokens`

> Opaque, revocable refresh tokens.

| Column | Type | Meaning |
|---|---|---|
| `token` | `text` PK | random 32-byte base64url string |
| `client_id` | `text` FK → `oauth_clients` ON DELETE CASCADE | |
| `user_id` | `uuid` FK → `app_users` ON DELETE CASCADE | |
| `scope` | `text` | inherited from auth code |
| `expires_at` | `timestamptz` NOT NULL | 30 days |
| `revoked` | `boolean` NOT NULL DEFAULT `false` | rotation marks the *old* one revoked |
| `created_at` | `timestamptz` NOT NULL DEFAULT `now()` | |

**Index:** `oauth_refresh_user_idx` on `(user_id)` — supports a "revoke all my sessions"
admin operation if you ever need it.

**Why opaque, not a JWT?** JWTs are stateless. We *want* refresh tokens to be revocable
on demand (e.g. if a user clicks "log out everywhere"). Opaque token + DB row gives us
that. Access tokens stay JWT for performance.

---

### A.3.5 `meals`

> Every meal a user logs.

| Column | Type | Meaning |
|---|---|---|
| `id` | `uuid` PK | |
| `user_id` | `uuid` FK → `app_users` ON DELETE CASCADE | |
| `name` | `text` NOT NULL | "Greek yogurt + berries" |
| `meal_type` | `text` | freeform but conventionally `breakfast`/`lunch`/`dinner`/`snack` |
| `calories` | `numeric` | |
| `protein_g`, `carbs_g`, `fat_g`, `fiber_g`, `sugar_g`, `sodium_mg` | `numeric` | macros, all optional |
| `notes` | `text` | |
| `consumed_at` | `timestamptz` NOT NULL DEFAULT `now()` | when the user actually ate it (UTC) |
| `created_at` | `timestamptz` NOT NULL DEFAULT `now()` | when the row was inserted |

**Note on two timestamps:** `consumed_at` ≠ `created_at`. The user can log a meal *after*
eating it ("log lunch from earlier"). All time-bucketed analytics filter on `consumed_at`.

**Index:** `meals_user_date_idx` on `(user_id, consumed_at desc)` — every nutrition
query is "give me meals for user X in date range Y", and `desc` matches the natural
sort order.

**RLS policy:** `using (user_id = auth.uid()) with check (user_id = auth.uid())`.
Both `using` (read) and `with check` (write) so a malicious user can't INSERT a row
with someone else's `user_id`.

---

### A.3.6 `water_logs`

> One row per glass / bottle. Cumulative totals computed at read time.

| Column | Type | Meaning |
|---|---|---|
| `id` | `uuid` PK | |
| `user_id` | `uuid` FK CASCADE | |
| `amount_ml` | `integer` NOT NULL CHECK (`amount_ml > 0`) | non-zero positive |
| `consumed_at` | `timestamptz` | |
| `created_at` | `timestamptz` | |

**Why not a single daily-total column?** Granularity. The LLM can show "you drank
500 ml at 09:14, then 250 ml at noon" — and totals are cheap to sum at read time.

**Index:** `water_logs_user_date_idx` on `(user_id, consumed_at desc)`.

---

### A.3.7 `nutrition_goals`

> One row per user. PK is `user_id` itself — no surrogate id needed for a singleton.

| Column | Type | Meaning |
|---|---|---|
| `user_id` | `uuid` PK + FK CASCADE | |
| `calories`, `protein_g`, `carbs_g`, `fat_g`, `fiber_g` | `numeric` | daily targets (any subset) |
| `water_ml` | `integer` | daily water target |
| `updated_at` | `timestamptz` NOT NULL DEFAULT `now()` | |

**Pattern: PK = FK.** This is how you express a 1:1 relationship in Postgres. Trying to
insert two goal rows for the same user fails on the PK constraint.

**Set on signup:** `oauth.py::authorize` does `upsert({"user_id": ...})` after creating
an account, so every user always has a (possibly empty) goals row.

---

### A.3.8 `exercises`

> The strength-training movement catalog. Two flavors:
> - **Global** rows: `user_id IS NULL`, seeded by the schema (28 rows).
> - **Custom** rows: `user_id = <some user>`, added by `add_custom_exercise`.

| Column | Type | Meaning |
|---|---|---|
| `id` | `uuid` PK | |
| `user_id` | `uuid` NULL, FK CASCADE | NULL = global |
| `name` | `text` NOT NULL | "Bench Press" |
| `category` | `text` | `strength` / `cardio` / `mobility` |
| `primary_muscle` | `text` | `chest` / `back` / `legs` / etc. |
| `equipment` | `text` | `barbell` / `dumbbell` / `cable` / `machine` / `bodyweight` |
| `is_custom` | `boolean` NOT NULL DEFAULT `false` | redundant with `user_id IS NOT NULL` but explicit |
| `created_at` | `timestamptz` | |

**Constraints:**
- `unique (user_id, name)` — same user can't have two custom exercises with the same name. Globals share `user_id = NULL` so collisions there are also blocked.

**Indexes:**
- `exercises_name_idx` on `(lower(name))` — case-insensitive search.

**RLS:** policy is `"own or global"` — `using (user_id is null or user_id = auth.uid())`.
This is the only non-strict-ownership policy in the schema, because the global catalog
must be visible to everyone.

**Used in code:**
- `tools/fitness.py::search_exercises` — `or_(f"user_id.is.null,user_id.eq.{u.user_id}")`
- `tools/fitness.py::add_custom_exercise` — INSERT with `user_id` set

---

### A.3.9 `workouts`

> A workout *session*. Container for sets.

| Column | Type | Meaning |
|---|---|---|
| `id` | `uuid` PK | |
| `user_id` | `uuid` FK CASCADE | |
| `name` | `text` | "Push Day", "Leg Day" |
| `notes` | `text` | how the user felt, etc. |
| `started_at` | `timestamptz` NOT NULL DEFAULT `now()` | when training began |
| `ended_at` | `timestamptz` | optional, for duration math |
| `duration_min` | `integer` | optional precomputed |
| `created_at` | `timestamptz` | |

**Index:** `workouts_user_date_idx` on `(user_id, started_at desc)` — trends and
"this week's workouts" queries.

---

### A.3.10 `workout_sets`

> Individual logged sets. Children of `workouts`. **Most analytic queries hit this table.**

| Column | Type | Meaning |
|---|---|---|
| `id` | `uuid` PK | |
| `workout_id` | `uuid` FK → `workouts` CASCADE | parent session |
| `user_id` | `uuid` FK → `app_users` CASCADE | duplicated for convenience (RLS + indexing) |
| `exercise_name` | `text` NOT NULL | **denormalized** — "Bench Press" |
| `exercise_id` | `uuid` FK → `exercises` ON DELETE SET NULL | catalog reference if matched |
| `set_number` | `integer` NOT NULL | 1, 2, 3, ... within this exercise within this workout |
| `reps` | `integer` | |
| `weight_kg` | `numeric` | |
| `rpe` | `numeric` | Rate of Perceived Exertion 1-10 (optional) |
| `is_warmup` | `boolean` NOT NULL DEFAULT `false` | warmup sets excluded from PR/volume queries |
| `notes` | `text` | |
| `created_at` | `timestamptz` | |

**Three indexes** — each tuned for a different query pattern:

| Index | Used by |
|---|---|
| `workout_sets_user_idx (user_id, created_at desc)` | "all my recent sets" |
| `workout_sets_exercise_idx (user_id, lower(exercise_name), created_at desc)` | `get_exercise_history`, PR lookups |
| `workout_sets_workout_idx (workout_id, set_number)` | rebuilding a workout's sets in display order |

**Why denormalize `exercise_name`?** Three reasons:

1. **Speed.** `get_exercise_history("Bench Press")` becomes a single index seek on
   `(user_id, lower(exercise_name), created_at desc)`. No join.
2. **Free-text exercises.** Users can type "Pendlay Row" without first creating a
   catalog entry. The set still gets logged with the name.
3. **Survivability.** If a catalog row is deleted, `exercise_id` is set to NULL but
   the set's history (`exercise_name`) remains intact.

The cost: renaming an exercise in the catalog *won't* propagate to historical sets.
For a personal tracker, that's the right tradeoff. For a multi-tenant SaaS where
exercises evolve, you'd want a versioned catalog.

**`ON DELETE SET NULL` on `exercise_id`:** deleting an exercise doesn't break history.
It just unlinks the set from the catalog.

---

### A.3.11 `cardio_sessions`

> Cardio activities — running, cycling, swimming, rowing, walking, HIIT.

| Column | Type | Meaning |
|---|---|---|
| `id` | `uuid` PK | |
| `user_id` | `uuid` FK CASCADE | |
| `activity` | `text` NOT NULL | freeform but typically one of the standard activities |
| `duration_min` | `numeric` NOT NULL CHECK (`> 0`) | |
| `distance_km` | `numeric` | optional |
| `calories` | `numeric` | optional |
| `avg_heart_rate`, `max_heart_rate` | `integer` | optional |
| `notes` | `text` | |
| `performed_at` | `timestamptz` | when the cardio happened |
| `created_at` | `timestamptz` | when the row was inserted |

**Index:** `cardio_user_date_idx` on `(user_id, performed_at desc)`.

---

### A.3.12 `step_logs`

> One row per user per local date. Composite primary key.

| Column | Type | Meaning |
|---|---|---|
| `user_id` | `uuid` NOT NULL, FK CASCADE | part of PK |
| `log_date` | `date` NOT NULL | part of PK — note: `date`, not `timestamptz` |
| `steps` | `integer` NOT NULL CHECK (`>= 0`) | |
| `distance_km` | `numeric` | optional |
| `calories` | `numeric` | optional |
| `updated_at` | `timestamptz` NOT NULL DEFAULT `now()` | |

**No `id` column.** The PK is `(user_id, log_date)`. Re-logging today's steps is an
upsert (`ON CONFLICT (user_id, log_date) DO UPDATE`).

**Why `date` instead of `timestamptz`?** Steps are inherently per-day in user-local
time. Storing as `timestamptz` would create timezone math on every read. Storing as
`date` says "this is a logical day in the user's timezone" and side-steps the issue.

**No indexes beyond the PK.** The PK index already covers every realistic query
(`WHERE user_id = ? AND log_date BETWEEN ? AND ?`).

---

### A.3.13 `body_metrics`

> Weight, body-fat percentage, circumferences. Sparse — most measurements are optional.

| Column | Type | Meaning |
|---|---|---|
| `id` | `uuid` PK | |
| `user_id` | `uuid` FK CASCADE | |
| `measured_at` | `timestamptz` NOT NULL DEFAULT `now()` | |
| `weight_kg` | `numeric` | optional |
| `body_fat_pct` | `numeric` | optional |
| `waist_cm`, `chest_cm`, `arm_cm`, `thigh_cm` | `numeric` | optional circumferences |
| `resting_hr` | `integer` | optional |
| `notes` | `text` | |
| `created_at` | `timestamptz` | |

**Sparse rows.** Tools require *at least one* metric (`log_body_metrics` validates),
but otherwise any subset is fine. Reads use `IS NOT NULL` filters.

**Index:** `body_metrics_user_date_idx` on `(user_id, measured_at desc)`.

---

### A.3.14 `fitness_goals`

> Mirror of `nutrition_goals` — one row per user, PK = `user_id`.

| Column | Type | Meaning |
|---|---|---|
| `user_id` | `uuid` PK + FK CASCADE | |
| `daily_steps` | `integer` | step target |
| `weekly_workouts` | `integer` | sessions per week |
| `weekly_cardio_min` | `integer` | minutes of cardio |
| `weekly_volume_kg` | `numeric` | total `reps × weight_kg` target |
| `target_weight_kg` | `numeric` | scale goal |
| `target_body_fat_pct` | `numeric` | composition goal |
| `updated_at` | `timestamptz` | |

Used by `get_fitness_progress` to compute percentages-to-goal for the LLM to surface.

---

## A.4 Cross-cutting design rules

### A.4.1 ON DELETE behavior

| Source | Target | Action |
|---|---|---|
| `app_users.id` | every `user_id` FK | **CASCADE** — deleting a user wipes all their data |
| `oauth_clients.client_id` | `oauth_auth_codes.client_id`, `oauth_refresh_tokens.client_id` | **CASCADE** |
| `workouts.id` | `workout_sets.workout_id` | **CASCADE** — deleting a workout removes its sets |
| `exercises.id` | `workout_sets.exercise_id` | **SET NULL** — preserve history when catalog changes |

The whole graph is deletable from the root: `DELETE FROM app_users WHERE id = ?` clears
**every** owned row across **every** table in a single statement. That's exactly what
`delete_account` relies on.

### A.4.2 Timestamps everywhere

Every meaningful event has a `timestamptz`. UTC in the column, IANA timezone on the
user, conversion at read time. This is a stricter pattern than "timestamp without time
zone" — it forces you to be explicit about offsets and prevents subtle off-by-one
issues at midnight boundaries.

### A.4.3 Two timestamp pattern

For events that the user can backfill — `meals`, `water_logs`, `cardio_sessions`,
`workouts`, `body_metrics` — there are **two** timestamps:

- `consumed_at` / `performed_at` / `started_at` / `measured_at` — the *real-world
  event time*.
- `created_at` — the *insertion time*.

Always filter analytics on the event time, not `created_at`. The LLM logging "lunch
from yesterday" sets event time to yesterday and `created_at` to now.

### A.4.4 Composite indexes always lead with `user_id`

Every per-user index starts with `user_id`. Why: every query has `WHERE user_id = ?`
in it (either explicitly in code or via RLS). A composite index with `user_id` as the
first column means Postgres can index-scan to the user's rows, then range-scan within
them by date.

### A.4.5 RLS policies — the matrix

| Table | RLS enabled? | Policy |
|---|---|---|
| `app_users` | no | server-only writes |
| `oauth_clients` | no | global, server-only |
| `oauth_auth_codes` | no | server-only, short-lived |
| `oauth_refresh_tokens` | no | server-only |
| `meals` | yes | `user_id = auth.uid()` |
| `water_logs` | yes | `user_id = auth.uid()` |
| `nutrition_goals` | yes | `user_id = auth.uid()` |
| `exercises` | yes | `user_id IS NULL OR user_id = auth.uid()` |
| `workouts` | yes | `user_id = auth.uid()` |
| `workout_sets` | yes | `user_id = auth.uid()` |
| `cardio_sessions` | yes | `user_id = auth.uid()` |
| `step_logs` | yes | `user_id = auth.uid()` |
| `body_metrics` | yes | `user_id = auth.uid()` |
| `fitness_goals` | yes | `user_id = auth.uid()` |

Service-role queries bypass RLS, but RLS is on as defense-in-depth. The day someone
accidentally exposes the anon key, the database still refuses to leak.

---

## A.5 Read patterns and which index serves them

| Query pattern (in tools) | Table | Index used |
|---|---|---|
| Today's meals | `meals` | `meals_user_date_idx` (range scan on `consumed_at`) |
| Date-range meal totals | `meals` | same |
| Today's water | `water_logs` | `water_logs_user_date_idx` |
| Goal progress (multi-table read) | `meals` + `water_logs` + `nutrition_goals` | their respective indexes; PK lookup on goals |
| All my recent workouts | `workouts` | `workouts_user_date_idx` |
| Sets for a specific workout | `workout_sets` | `workout_sets_workout_idx` |
| `get_exercise_history("Bench Press")` | `workout_sets` | `workout_sets_exercise_idx` |
| `get_personal_records()` (all exercises) | `workout_sets` | `workout_sets_user_idx`, then in-memory aggregate |
| Trend window comparison | multiple | each table's `(user_id, date desc)` index |
| Step history | `step_logs` | PK `(user_id, log_date)` |

For analytics that scan thousands of rows (e.g. `get_personal_records` over a year of
training), the right scaling move is a **materialized view** that pre-computes the
heaviest weight and best e1RM per `(user_id, exercise_name)`. Refresh nightly.

---

## A.6 Common admin queries (cheat sheet)

These are useful in the Supabase SQL editor for debugging.

```sql
-- Are there orphan workout_sets (FK should prevent this)?
SELECT count(*) FROM workout_sets ws
LEFT JOIN workouts w ON w.id = ws.workout_id
WHERE w.id IS NULL;

-- Top 10 heaviest sets across all users (for sanity-checking PR logic)
SELECT user_id, exercise_name, weight_kg, reps, created_at
FROM workout_sets
WHERE NOT is_warmup AND weight_kg IS NOT NULL
ORDER BY weight_kg DESC
LIMIT 10;

-- How many tokens does each user have outstanding?
SELECT user_id, count(*) AS active_refresh
FROM oauth_refresh_tokens
WHERE NOT revoked AND expires_at > now()
GROUP BY user_id;

-- Stale auth codes (cleanup candidates)
SELECT count(*) FROM oauth_auth_codes
WHERE consumed = true OR expires_at < now() - interval '1 day';

-- Per-user data footprint
SELECT
  u.email,
  (SELECT count(*) FROM meals m WHERE m.user_id = u.id) AS meals,
  (SELECT count(*) FROM workouts w WHERE w.user_id = u.id) AS workouts,
  (SELECT count(*) FROM workout_sets s WHERE s.user_id = u.id) AS sets,
  (SELECT count(*) FROM cardio_sessions c WHERE c.user_id = u.id) AS cardio
FROM app_users u
ORDER BY workouts DESC;
```

---

## A.7 Why these design choices — a defendable list for interviews

1. **`uuid` primary keys, not `bigint`.** No collision risk if databases are ever
   merged, no enumerable IDs in URLs, generated server-side.
2. **Composite PK on `step_logs`** because a step log is conceptually identified by
   *who* and *when* — a surrogate key would be redundant and let bugs create duplicates.
3. **`nutrition_goals` and `fitness_goals` use `user_id` as the PK**, expressing 1:1
   structurally rather than via a CHECK or trigger.
4. **Denormalized `exercise_name` on `workout_sets`** trades a small write-time cost
   (copying the name) for major read-time wins (no join on hot queries) and resilience
   (catalog deletes don't break history).
5. **Two timestamps per event** (`consumed_at` vs `created_at`) supports backfill and
   audit at the same time — analytics use the former, audits the latter.
6. **`ON DELETE CASCADE` everywhere except `exercises -> workout_sets`** because a
   training history outlives the catalog.
7. **RLS on all per-user tables** is unused at runtime (we go through service-role)
   but is the safety net if any future code path uses the anon key.
8. **Idempotent schema** — every `create table` is `if not exists`, every `create
   policy` wrapped in `do $$ begin ... exception when duplicate_object ... end $$`.
   You can re-run `schema.sql` against a populated database without breaking it.

---

## A.8 What scaling further would look like

This section is for the interviewer who asks *"how would this hold up at 100k users?"*

- **`workout_sets` is the hottest table.** Add a partitioning scheme: monthly partitions
  on `created_at`, or hash-partition by `user_id` if cross-user reporting matters.
- **Trends and PRs become expensive at scale.** Build materialized views:
  - `mv_pr_per_user_exercise` (max weight, max e1RM per user × exercise) — refresh nightly.
  - `mv_daily_volume` (sum `reps * weight_kg` per user per day) — drives weekly rollups.
- **OAuth tables** stay small — millions of rows is fine. Add a daily VACUUM or
  `pg_cron` job to expire consumed/expired codes and revoked tokens past 60 days.
- **Time-bucketed queries** are the textbook use case for **TimescaleDB** (Postgres
  extension) — convert `meals`, `workout_sets`, `cardio_sessions` to hypertables.
- **Read replicas** for analytics queries; primary handles writes only.
- **Connection pooling** at scale: PgBouncer or Supabase's built-in PgBouncer endpoint.

---

## Quick pacing guide

If you have **one focused hour:** files 1, 2, 6, 7, 11. You'll grasp the architecture
and the OAuth flow.

If you have **a half-day:** read all of it in this order. Take notes on the parts that
surprised you.

For **interview prep specifically**, the three highest-leverage files are:
- `supabase/schema.sql` (so you can sketch the data model on a whiteboard)
- `src/auth/oauth.py` (the "walk me through your auth" answer lives here)
- `src/app.py` (the "interesting routing decision" answer lives here)
