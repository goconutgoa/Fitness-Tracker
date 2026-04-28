"""MCP prompts — predefined templates the user invokes from Claude's UI.

Prompts in MCP are *user-invoked* (vs tools, which the model invokes). They
appear in the Claude.ai connector as quick-launch buttons. Each prompt returns
a starter message that tells the model exactly which tools to call and how to
present the result, so the user never has to type the same long instruction twice.

Naming convention: ``<scope>_<action>`` (e.g. ``daily_nutrition_report``).
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:

    # ------------------------------------------------------------------
    # Nutrition — the original request
    # ------------------------------------------------------------------
    @mcp.prompt(
        name="daily_nutrition_report",
        title="Daily nutrition report",
        description=(
            "Today's macro progress with a visual progress bar, what I've eaten so "
            "far, time-of-day-aware suggestions, and tips to hit my goals."
        ),
    )
    def daily_nutrition_report() -> str:
        return """\
Generate a complete daily nutrition report for me. Use these tools in order:

1. `get_timezone` — so you know what local time it is for me right now.
2. `get_goal_progress` — my targets vs consumed for today.
3. `get_meals_today` — every meal I've eaten today with macros.
4. `get_water_today` — water intake so far today.

Then produce a clean, scannable report with these sections:

**1. Progress bars**
For each of: calories, protein, carbs, fat, fiber, water — render an ASCII
progress bar like:
```
Protein   [████████░░░░░░░░░░░░]  65g / 120g   54%   need 55g more
Calories  [████████████░░░░░░░░] 1450 / 2200  66%   need 750 more
```
Use 20 characters wide. Round percentages to whole numbers. If a goal isn't set
for a macro, just show the consumed amount.

**2. What I've eaten so far**
Group by meal_type (breakfast / lunch / dinner / snack). For each meal show
name + key macros. At the end, call out which meal contributed the most
protein, the most carbs, and the most fat.

**3. Time-of-day awareness**
Based on the current local time:
  - **Morning (before 11am)** — say what a balanced day would look like from
    here, given my goals.
  - **Midday (11am–4pm)** — flag any macro I'm dangerously off-pace on. The
    pace check: at 1pm I should be ~50% to most goals.
  - **Evening (4–9pm)** — recommend a specific final meal that closes the
    biggest gap.
  - **Late night (after 9pm)** — be honest about what's still achievable
    without overshooting calories.

**4. Three actionable tips**
Specific, food-level recommendations. Not "eat more protein" — instead "you're
40g short on protein; a 150g grilled chicken breast (~45g protein, ~250 kcal)
would close it." Reference foods I've already eaten today when relevant
(e.g. "you ate Greek yogurt at breakfast — pair tomorrow's with berries to
add the fiber you missed today").

**5. Hydration**
Quick line on where my water intake stands and what's reasonable to finish.

**6. One-line morale**
A single sentence based on how on-track I am — encouraging but honest.
"""

    # ------------------------------------------------------------------
    # Fitness — mirror of the nutrition report
    # ------------------------------------------------------------------
    @mcp.prompt(
        name="daily_fitness_report",
        title="Daily fitness report",
        description=(
            "Today's training: workouts done, sets/volume, cardio, steps, and how "
            "they ladder into weekly fitness goals."
        ),
    )
    def daily_fitness_report() -> str:
        return """\
Generate a complete daily fitness report. Use these tools in order:

1. `get_timezone` — current local time.
2. `get_workouts_today` — sessions logged today with their sets.
3. `get_cardio_by_date` — today's cardio (run with no `day` arg = today).
4. `get_steps_today` — today's step count.
5. `get_fitness_progress` — weekly goal progress (workouts, cardio min, volume,
   steps).

Produce a report with:

**1. Today at a glance**
A 4-line summary:
  - Workouts: <count> session(s), <total volume kg> kg total volume
  - Cardio: <minutes> min, <km>, <calories> kcal
  - Steps: <count> / <goal> (<percent>%)
  - Sets logged: <count> working sets

**2. Working sets breakdown**
For each workout today, show name, duration, and a per-exercise summary
(`Bench Press: 4×5 @ 80kg, top set 5 reps`). Highlight any set that beats
my previous heaviest for that exercise — that's a PR signal worth calling out.

**3. Weekly goal progress** (from `get_fitness_progress`)
Render progress bars for: weekly workouts, weekly cardio minutes, weekly
volume kg, daily steps. Same 20-char ASCII bar format as the nutrition report.

**4. Time-of-day awareness**
  - **Morning** — note what's still possible today; suggest a session if I
    haven't trained yet and weekly volume is behind.
  - **Afternoon** — celebrate what's done; flag a step gap if it's big.
  - **Evening** — wind down, say whether I'm on track for the week.

**5. Three suggestions**
Concrete next actions: a missing muscle group this week, a step shortfall I
could close with a 20-minute walk, a recovery note if I've trained 3 days
in a row.

**6. One-line morale.**
"""

    # ------------------------------------------------------------------
    # Weekly review — combined nutrition + fitness retrospective
    # ------------------------------------------------------------------
    @mcp.prompt(
        name="weekly_review",
        title="Weekly review",
        description=(
            "7-day combined nutrition + fitness retrospective with trends, PR "
            "highlights, and what to focus on next week."
        ),
    )
    def weekly_review() -> str:
        return """\
Generate a 7-day combined nutrition + fitness review. Use these tools:

1. `get_timezone`
2. `get_nutrition_summary` with days=7
3. `get_trends` with days=14 — recent vs prior comparison for nutrition
4. `get_fitness_summary` with days=7
5. `get_workout_trends` with days=14 — recent vs prior comparison for training
6. `get_personal_records`
7. `get_consistency_streak`
8. `get_body_metrics_history` with days=14 — to see if weight is moving

Build the review in this order:

**1. The week in numbers** (4 lines)
  - Avg calories/day, avg protein/day
  - Workouts: <count>, total volume <kg>, cardio <min>
  - Steps avg/day, current streak <days>
  - Body weight change (if logged)

**2. What changed vs the prior 7 days**
Use the trend tools' delta numbers. Call out the *three* biggest movers
(positive or negative). Be precise — "+12.3% in weekly volume" not "trending up".

**3. PR highlights**
List any new personal records from this week. If none, list the closest near-PR
attempts.

**4. Consistency**
Streak status, what would extend it, what would break it.

**5. What to focus on next week** (3 specific goals)
Each goal = a number + a concrete behavior. For example:
  - "Hit 130g protein on at least 5 days (last week: 3 days)"
  - "Add one upper-body session — pulling volume was 40% below recent average"
  - "Close the step gap by walking 30 min on each rest day"

**6. One paragraph reflection**
Two to three sentences synthesising how the week went and the trajectory.
"""

    # ------------------------------------------------------------------
    # Forward-looking — meal suggestion that fills the macro gap
    # ------------------------------------------------------------------
    @mcp.prompt(
        name="meal_suggestion",
        title="Suggest a meal",
        description=(
            "Recommend 3 meal ideas that fit my remaining macro budget for today, "
            "based on what I've already eaten and what's left of my goal."
        ),
    )
    def meal_suggestion() -> str:
        return """\
Recommend three meal ideas tailored to my remaining macro budget for today.

1. Call `get_goal_progress` to get my consumed-vs-goal numbers.
2. Call `get_meals_today` so you can avoid suggesting things I just ate.

Then propose **three distinct meals** that, if eaten now, would together
make solid progress toward closing the largest macro gap (usually protein
or fiber). For each meal:
  - Name + 1-line description
  - Estimated macros: calories / protein_g / carbs_g / fat_g
  - Why it fits — which gap it closes
  - Approximate prep time

After the three options, give a 1-sentence top recommendation. Don't be
generic — if I'm 5g over on fat already, don't suggest something fried.
"""
