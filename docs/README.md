# Database diagrams

Rendered images of the Supabase / Postgres schema. Each is provided as both PNG
(easy to view inline, paste into slides) and SVG (vector — zoom infinitely without
blur, best for studying details).

## What each file shows

| File | Purpose | Best for |
|---|---|---|
| `schema-overview.png` / `.svg` | All 14 tables at a glance, color-coded by cluster (orange=root, purple=auth, green=nutrition, blue=fitness, gold=goals). Shows relationships, hides column lists. | Whiteboard / interview opener |
| `schema-auth.png` / `.svg` | OAuth + identity tables (`app_users`, `oauth_clients`, `oauth_auth_codes`, `oauth_refresh_tokens`) with every column and constraint. | Explaining the OAuth provider |
| `schema-nutrition.png` / `.svg` | Nutrition cluster (`meals`, `water_logs`, `nutrition_goals`) with full columns. | Walking through a meal-logging request |
| `schema-fitness.png` / `.svg` | Fitness cluster (`exercises`, `workouts`, `workout_sets`, `cardio_sessions`, `step_logs`, `body_metrics`, `fitness_goals`) with full columns. | Explaining workout/PR/streak analytics |

## How to view

- **PNG** — open in any image viewer, drag into Slack/Notion, paste into slides.
- **SVG** — open in any modern browser. Use Ctrl+/Cmd+ to zoom infinitely without
  losing sharpness. Recommended for reading column-level detail on a small screen.

## How they were generated

Each `.mmd` is a Mermaid source file. Render with the Mermaid CLI:

```bash
npx -y -p @mermaid-js/mermaid-cli mmdc \
  -i schema-overview.mmd -o schema-overview.png \
  -t default -b white --width 1800 --height 1400 --scale 2
```

To regenerate everything after editing a `.mmd`:

```bash
cd docs
for name in schema-overview schema-auth schema-nutrition schema-fitness; do
  npx -y -p @mermaid-js/mermaid-cli mmdc -i "$name.mmd" -o "$name.png" \
      -t default -b white --width 1800 --height 1400 --scale 2
  npx -y -p @mermaid-js/mermaid-cli mmdc -i "$name.mmd" -o "$name.svg" \
      -t default -b white
done
```

## Editing online (no install)

Paste any `.mmd` content into <https://mermaid.live> for an interactive editor.

For an alternative-format version of the schema, see `STUDY_GUIDE.md` Appendix A
which also has the schema as DBML / ASCII.
