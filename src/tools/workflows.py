"""Workflow tools — the same instruction templates as ``src/prompts/templates.py``,
exposed as MCP **tools** so they surface in Claude.ai's connector UI.

Why this exists: Claude.ai's custom-connector UI (April 2026) only renders MCP
**tools** — MCP prompts work in Claude Code as ``/mcp__server__prompt`` slash
commands, but Claude.ai web/desktop doesn't yet expose them. Wrapping each
prompt as a tool gives users a way to trigger predefined workflows from
Claude.ai while keeping the prompts themselves available for clients that
support them.

When the user asks "run my daily nutrition report" (or similar), Claude calls
the matching workflow tool. The tool returns the instruction text, and Claude
treats that as a system-level directive for the rest of the turn — calling
the data tools (``get_meals_today`` etc.) and producing the requested report.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

# Single source of truth for the instruction text. The prompt module re-uses
# these so Claude Code users get the same content via /mcp slash commands.
DAILY_NUTRITION_REPORT = """\
Generate a complete daily nutrition report. Use these tools in order:

1. `get_timezone` to get the user's local time.
2. `get_goal_progress` for targets vs consumed today.
3. `get_meals_today` for every meal eaten today with macros.
4. `get_water_today` for water intake so far.

Then produce these sections:

**1. Progress bars** — for calories, protein, carbs, fat, fiber, water, render
ASCII bars 20 characters wide, e.g.:
```
Protein   [████████░░░░░░░░░░░░]  65g / 120g   54%   need 55g more
```
If a goal isn't set for a macro, just show consumed.

**2. What I've eaten so far** — group by meal_type. For each meal: name + key
macros. Call out the meal contributing the most protein, carbs, and fat.

**3. Time-of-day awareness** — based on the local time:
  - Before 11am: lay out a balanced rest-of-day to hit goals.
  - 11am-4pm: flag any macro dangerously off-pace (50% target by 1pm).
  - 4-9pm: recommend a specific final meal that closes the biggest gap.
  - After 9pm: be honest about what's still achievable without overshooting.

**4. Three actionable tips** — food-level, not generic. e.g.: "you're 40g
short on protein; a 150g grilled chicken breast (~45g protein, ~250 kcal)
would close it." Reference foods already eaten when relevant.

**5. Hydration** — short line on water vs goal.

**6. One-line morale** — encouraging but honest based on how on-track.
"""

DAILY_FITNESS_REPORT = """\
Generate a complete daily fitness report. Use these tools in order:

1. `get_timezone`
2. `get_workouts_today`
3. `get_cardio_by_date` (no `day` arg = today)
4. `get_steps_today`
5. `get_fitness_progress`

Produce:

**1. Today at a glance** (4 lines)
  - Workouts: <count> session(s), <volume> kg total
  - Cardio: <minutes> min, <km>, <calories> kcal
  - Steps: <count> / <goal> (<percent>%)
  - Sets logged: <count> working sets

**2. Working sets breakdown** — per workout, per exercise summary
(e.g. "Bench Press: 4×5 @ 80kg, top set 5 reps"). Call out any set that
beats my previous heaviest for that exercise — PR signal.

**3. Weekly goal progress** — render 20-char ASCII progress bars for:
weekly workouts, weekly cardio min, weekly volume kg, daily steps.

**4. Time-of-day awareness**
  - Morning: what's still possible today; suggest a session if behind on weekly volume.
  - Afternoon: celebrate what's done; flag step gaps.
  - Evening: wind down; say whether on-track for the week.

**5. Three concrete suggestions** — a missing muscle group, a step shortfall
that a 20-min walk closes, a recovery note if 3 sessions in a row.

**6. One-line morale.**
"""

WEEKLY_REVIEW = """\
Generate a 7-day combined nutrition + fitness review. Use these tools:

1. `get_timezone`
2. `get_nutrition_summary` with days=7
3. `get_trends` with days=14 (recent vs prior nutrition)
4. `get_fitness_summary` with days=7
5. `get_workout_trends` with days=14 (recent vs prior training)
6. `get_personal_records`
7. `get_consistency_streak`
8. `get_body_metrics_history` with days=14

Build the review:

**1. The week in numbers** (4 lines)
  - Avg calories/day, avg protein/day
  - Workouts <count>, total volume <kg>, cardio <min>
  - Steps avg/day, current streak <days>
  - Body weight change (if logged)

**2. What changed vs the prior 7 days** — call out the THREE biggest movers
with precise numbers (e.g. "+12.3% in weekly volume"), not "trending up".

**3. PR highlights** — list new PRs from this week, or closest near-PRs.

**4. Consistency** — streak status, what would extend / break it.

**5. What to focus on next week** (3 specific goals)
Each goal = number + behavior. e.g.:
  - "Hit 130g protein on at least 5 days (last week: 3 days)"
  - "Add one upper-body session — pulling volume was 40% below recent avg"

**6. One-paragraph reflection** — 2-3 sentences synthesising trajectory.
"""

MEAL_SUGGESTION = """\
Recommend three meal ideas for the rest of today.

1. Call `get_goal_progress` for consumed vs goal numbers.
2. Call `get_meals_today` so you avoid suggesting things just eaten.

Then propose **three distinct meals** that, if eaten now, would together close
the largest macro gap (usually protein or fiber). For each:
  - Name + 1-line description
  - Estimated macros: calories / protein_g / carbs_g / fat_g
  - Why it fits — which gap it closes
  - Approximate prep time

End with a 1-sentence top recommendation. Don't be generic — if I'm 5g over
on fat already, don't suggest fried food.
"""


def register(mcp: FastMCP) -> None:

    @mcp.tool(
        description=(
            "Daily nutrition workflow. Returns instructions to produce a complete "
            "daily nutrition report — progress bars per macro, meals so far with "
            "contributions, time-of-day-aware suggestions, hydration, and tips. "
            "Call this when the user asks for their daily nutrition report, "
            "macro check, or 'how am I doing on food today'."
        )
    )
    def daily_nutrition_report() -> str:
        return DAILY_NUTRITION_REPORT

    @mcp.tool(
        description=(
            "Daily fitness workflow. Returns instructions to produce a daily "
            "training report — today's workouts/sets/volume, cardio, steps, "
            "weekly-goal progress, PR call-outs, and time-of-day suggestions. "
            "Call this when the user asks 'how's my training today', for a "
            "fitness summary, or for a daily training report."
        )
    )
    def daily_fitness_report() -> str:
        return DAILY_FITNESS_REPORT

    @mcp.tool(
        description=(
            "Weekly review workflow. Returns instructions to produce a 7-day "
            "combined nutrition + fitness retrospective with deltas vs the "
            "prior week, PR highlights, three focus goals for next week, and "
            "a one-paragraph reflection. Call this when the user asks for a "
            "weekly review, weekly recap, or 'how was my week'."
        )
    )
    def weekly_review() -> str:
        return WEEKLY_REVIEW

    @mcp.tool(
        description=(
            "Meal suggestion workflow. Returns instructions to recommend three "
            "meal ideas tailored to the user's remaining macro budget for "
            "today, avoiding what they've already eaten. Call this when the "
            "user asks 'what should I eat next', for meal ideas, or for "
            "snack/dinner suggestions."
        )
    )
    def meal_suggestion() -> str:
        return MEAL_SUGGESTION
