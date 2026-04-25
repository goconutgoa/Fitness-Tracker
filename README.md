# Fitness + Nutrition MCP

A remote [Model Context Protocol](https://modelcontextprotocol.io/) server for
personal **nutrition** and **full-body fitness** tracking.

Python port of [akutishevsky/nutrition-mcp](https://github.com/akutishevsky/nutrition-mcp),
extended with strength training, cardio, steps, body metrics, PRs, and longitudinal
progression analytics.

- **Runtime:** Python 3.12, FastMCP + Starlette + Uvicorn
- **Database:** Supabase (Postgres) with RLS
- **Auth:** OAuth 2.0 (authorization code + PKCE, dynamic client registration)
- **Transport:** MCP streamable HTTP
- **Deploy target:** Render free web service (also works anywhere Docker runs)

## What it does

### Nutrition (mirrors nutrition-mcp)
- Log / update / delete meals with full macros
- Log / delete water intake
- Daily / date-range meal views with running totals
- Nutrition goals + today's progress (with % to goal and remaining)
- N-day summary, trend comparison (recent vs prior window)
- Meal patterns by day-of-week and meal type
- Timezone (IANA) + account deletion

### Fitness
- **Strength:** log full workouts in one call — each set with reps, weight, RPE,
  warmup flag, notes. Catalog of ~30 seeded exercises + per-user custom exercises.
- **Cardio:** running / cycling / swimming / rowing / walking / HIIT, with duration,
  distance, calories, avg/max HR.
- **Steps:** daily log (one row per day, upsert).
- **Body metrics:** weight, body-fat %, circumferences, resting HR.
- **Goals:** daily steps, weekly workouts, weekly cardio minutes, weekly total
  volume (kg), target body weight / body-fat %.
- **Progression:**
  - Per-exercise history (every working set, oldest → newest)
  - Personal records — heaviest weight per exercise + best estimated 1RM (Epley)
  - Workout trends — recent vs prior window delta for volume, sets, cardio, steps
  - Patterns — by primary muscle group, top exercises
  - Consistency streak (workouts or step-goal days)
  - Rolling N-day fitness summary

## Setup

### 1. Create a Supabase project

1. [Create a new Supabase project](https://app.supabase.com/new).
2. Open SQL Editor → paste the contents of [`supabase/schema.sql`](supabase/schema.sql)
   → run.
3. Settings → API: copy the **Project URL** and **service_role** key.

### 2. Local dev

```bash
cp .env.example .env
# edit .env: paste Supabase URL + service key, generate secrets
python -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET
python -c "import secrets; print(secrets.token_urlsafe(48))"   # SESSION_SECRET

pip install -r requirements.txt
uvicorn src.app:app --reload --port 8080
```

Visit `http://localhost:8080` — the landing page shows the connector URL.

### 3. Deploy to Render

1. Push this repo to GitHub.
2. On [render.com](https://render.com) → **New → Web Service** → pick the repo.
3. Render auto-detects [`render.yaml`](render.yaml). Free plan, Docker runtime.
4. Add the required secrets in the dashboard (or let Render generate
   `JWT_SECRET` / `SESSION_SECRET` from `render.yaml`):
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
   - `PUBLIC_BASE_URL` → set to your Render URL, e.g.
     `https://fitness-nutrition-mcp.onrender.com`
5. Deploy. First cold start takes ~30 s; Render free tier sleeps after 15 min
   of inactivity — Claude will wake it up on the next request.

### 4. Connect from Claude

1. In Claude.ai → **Settings → Connectors → Add custom connector**.
2. Paste `https://<your-render-url>/mcp`.
3. Sign up with an email + password on first connect.
4. You're done — ask Claude to log a meal or a workout.

## Testing

**Offline smoke test** (no Supabase required — verifies imports, tool
registration, OAuth metadata, and bearer-auth middleware):

```bash
pip install -r requirements.txt
python scripts/smoke.py
```

**End-to-end** (against a running server with a real Supabase backing it):

```bash
python scripts/e2e.py --base-url http://localhost:8080 \
    --email you+e2e@example.com --password supersecret123
```

Both run in CI on every push (see [`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

## OAuth endpoints (for reference)

| Endpoint                                               | Purpose                          |
|--------------------------------------------------------|----------------------------------|
| `GET  /.well-known/oauth-authorization-server`         | RFC 8414 metadata                |
| `POST /oauth/register`                                 | RFC 7591 dynamic client registration |
| `GET/POST /oauth/authorize`                            | Login + authorization code grant |
| `POST /oauth/token`                                    | Code → tokens + refresh          |
| `POST /mcp`                                            | MCP streamable HTTP (Bearer auth)|

## Repo layout

```
src/
├── app.py                 # Starlette composition
├── mcp_server.py          # FastMCP + tool registration
├── config.py              # env-driven settings
├── context.py             # current_user ContextVar
├── auth/
│   ├── oauth.py           # OAuth provider (authorize/token/register/metadata)
│   ├── middleware.py      # Bearer-token ASGI middleware
│   ├── tokens.py          # JWT issue/verify
│   └── passwords.py       # bcrypt hash/verify
├── db/client.py           # Supabase singleton
└── tools/
    ├── helpers.py         # user/tz, local-day bounds, macros
    ├── nutrition.py       # all nutrition tools
    └── fitness.py         # all fitness tools
supabase/
└── schema.sql             # tables, RLS, seed exercise catalog
Dockerfile
render.yaml
```

## Example prompts once connected

- *"Log breakfast: 3 eggs, 2 slices whole-wheat toast, black coffee."*
- *"Log today's push workout: bench 80 kg × 5, 5, 4, 3. Incline DB press 28 kg × 10, 10, 9. Cable fly 20 kg × 12, 12, 12."*
- *"I walked 9,240 steps today."*
- *"How's my bench pressing progressed over the last two months?"*
- *"Am I on track for my weekly volume goal?"*
- *"Compare this week's training to last week."*
- *"What's my current consistency streak?"*

## License

MIT
