"""Fitness MCP tools.

Design goals driven by the user's "progressive improvement over time is key":
  * Every logged set carries denormalized exercise_name → fast PR & history lookups.
  * Trend endpoints always compare windows so the LLM can surface deltas.
  * One-set PRs tracked as heaviest weight per rep count AND estimated 1RM.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytz
from mcp.server.fastmcp import FastMCP

from ..context import require_user
from ..db import get_supabase
from .helpers import iso, local_day_bounds, parse_date, user_and_tz


def epley_1rm(weight: float, reps: int) -> float:
    """Epley formula — simple, widely used. Returns estimated 1RM."""
    if reps <= 0:
        return 0.0
    if reps == 1:
        return weight
    return round(weight * (1 + reps / 30), 2)


def register(mcp: FastMCP) -> None:

    # ---------- exercises ----------
    @mcp.tool(description="Search the exercise catalog (global + custom). Leave query empty to list all.")
    def search_exercises(query: str = "", category: str | None = None, muscle: str | None = None) -> list[dict]:
        u = require_user()
        sb = get_supabase()
        q = sb.table("exercises").select("*").or_(f"user_id.is.null,user_id.eq.{u.user_id}")
        if query:
            q = q.ilike("name", f"%{query}%")
        if category:
            q = q.eq("category", category)
        if muscle:
            q = q.eq("primary_muscle", muscle)
        return (q.order("name").execute().data) or []

    @mcp.tool(description="Add a custom exercise (user-specific). Returns the inserted exercise.")
    def add_custom_exercise(
        name: str,
        primary_muscle: str | None = None,
        equipment: str | None = None,
        category: str = "strength",
    ) -> dict:
        u = require_user()
        row = {
            "user_id": u.user_id, "name": name, "category": category,
            "primary_muscle": primary_muscle, "equipment": equipment, "is_custom": True,
        }
        return get_supabase().table("exercises").insert(row).execute().data[0]

    # ---------- workouts ----------
    @mcp.tool(description=(
        "Log a full workout session in one call. `sets` is a list of "
        "{exercise, reps, weight_kg, rpe?, is_warmup?, notes?}. Returns the workout with all sets."
    ))
    def log_workout(
        sets: list[dict],
        name: str | None = None,
        notes: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        duration_min: int | None = None,
    ) -> dict:
        u = require_user()
        if not sets:
            raise ValueError("sets must contain at least one set")
        sb = get_supabase()

        w_payload = {"user_id": u.user_id, "name": name, "notes": notes, "duration_min": duration_min}
        if started_at:
            w_payload["started_at"] = started_at
        if ended_at:
            w_payload["ended_at"] = ended_at
        workout = sb.table("workouts").insert(w_payload).execute().data[0]

        # group by exercise to assign set_number
        counters: dict[str, int] = {}
        rows = []
        for s in sets:
            ex = (s.get("exercise") or s.get("exercise_name") or "").strip()
            if not ex:
                raise ValueError("each set requires 'exercise'")
            counters[ex] = counters.get(ex, 0) + 1
            rows.append({
                "workout_id": workout["id"],
                "user_id": u.user_id,
                "exercise_name": ex,
                "exercise_id": s.get("exercise_id"),
                "set_number": counters[ex],
                "reps": s.get("reps"),
                "weight_kg": s.get("weight_kg"),
                "rpe": s.get("rpe"),
                "is_warmup": bool(s.get("is_warmup", False)),
                "notes": s.get("notes"),
            })
        inserted = sb.table("workout_sets").insert(rows).execute().data
        workout["sets"] = inserted
        return workout

    @mcp.tool(description="Add more sets to an existing workout.")
    def add_sets_to_workout(workout_id: str, sets: list[dict]) -> list[dict]:
        u = require_user()
        sb = get_supabase()
        # count existing per exercise so new set_numbers continue correctly
        existing = sb.table("workout_sets").select("exercise_name,set_number").eq("workout_id", workout_id).eq("user_id", u.user_id).execute().data or []
        counters: dict[str, int] = {}
        for r in existing:
            counters[r["exercise_name"]] = max(counters.get(r["exercise_name"], 0), r["set_number"])
        rows = []
        for s in sets:
            ex = (s.get("exercise") or s.get("exercise_name") or "").strip()
            if not ex:
                raise ValueError("each set requires 'exercise'")
            counters[ex] = counters.get(ex, 0) + 1
            rows.append({
                "workout_id": workout_id, "user_id": u.user_id, "exercise_name": ex,
                "set_number": counters[ex], "reps": s.get("reps"), "weight_kg": s.get("weight_kg"),
                "rpe": s.get("rpe"), "is_warmup": bool(s.get("is_warmup", False)), "notes": s.get("notes"),
            })
        return sb.table("workout_sets").insert(rows).execute().data or []

    @mcp.tool(description="Delete a workout (and all its sets).")
    def delete_workout(workout_id: str) -> dict:
        u = require_user()
        get_supabase().table("workouts").delete().eq("id", workout_id).eq("user_id", u.user_id).execute()
        return {"deleted": True, "id": workout_id}

    @mcp.tool(description="Delete an individual set.")
    def delete_set(set_id: str) -> dict:
        u = require_user()
        get_supabase().table("workout_sets").delete().eq("id", set_id).eq("user_id", u.user_id).execute()
        return {"deleted": True, "id": set_id}

    @mcp.tool(description="Get today's workouts with their sets.")
    def get_workouts_today() -> dict:
        uid, tz = user_and_tz()
        start, end = local_day_bounds(tz)
        return _fetch_workouts(uid, tz, start, end, label=start.astimezone(pytz.timezone(tz)).date().isoformat())

    @mcp.tool(description="Get workouts on a specific date (YYYY-MM-DD).")
    def get_workouts_by_date(day: str) -> dict:
        uid, tz = user_and_tz()
        start, end = local_day_bounds(tz, parse_date(day))
        return _fetch_workouts(uid, tz, start, end, label=day)

    @mcp.tool(description="Get workouts within a date range (inclusive).")
    def get_workouts_by_date_range(start_date: str, end_date: str) -> dict:
        uid, tz = user_and_tz()
        s, _ = local_day_bounds(tz, parse_date(start_date))
        _, e = local_day_bounds(tz, parse_date(end_date))
        return _fetch_workouts(uid, tz, s, e, label=f"{start_date}..{end_date}")

    # ---------- cardio ----------
    @mcp.tool(description="Log a cardio session (running, cycling, swimming, rowing, walking, HIIT, etc.).")
    def log_cardio(
        activity: str,
        duration_min: float,
        distance_km: float | None = None,
        calories: float | None = None,
        avg_heart_rate: int | None = None,
        max_heart_rate: int | None = None,
        notes: str | None = None,
        performed_at: str | None = None,
    ) -> dict:
        u = require_user()
        if duration_min <= 0:
            raise ValueError("duration_min must be > 0")
        payload = {
            "user_id": u.user_id, "activity": activity, "duration_min": duration_min,
            "distance_km": distance_km, "calories": calories,
            "avg_heart_rate": avg_heart_rate, "max_heart_rate": max_heart_rate, "notes": notes,
        }
        if performed_at:
            payload["performed_at"] = performed_at
        return get_supabase().table("cardio_sessions").insert(payload).execute().data[0]

    @mcp.tool(description="Delete a cardio session.")
    def delete_cardio(cardio_id: str) -> dict:
        u = require_user()
        get_supabase().table("cardio_sessions").delete().eq("id", cardio_id).eq("user_id", u.user_id).execute()
        return {"deleted": True, "id": cardio_id}

    @mcp.tool(description="Get cardio sessions for a date (default today).")
    def get_cardio_by_date(day: str | None = None) -> dict:
        uid, tz = user_and_tz()
        d = parse_date(day) if day else None
        start, end = local_day_bounds(tz, d)
        r = get_supabase().table("cardio_sessions").select("*").eq("user_id", uid).gte("performed_at", iso(start)).lt("performed_at", iso(end)).order("performed_at").execute()
        rows = r.data or []
        return {
            "date": start.astimezone(pytz.timezone(tz)).date().isoformat(),
            "sessions": rows,
            "total_duration_min": sum((x.get("duration_min") or 0) for x in rows),
            "total_distance_km": round(sum((x.get("distance_km") or 0) for x in rows), 2),
            "total_calories": round(sum((x.get("calories") or 0) for x in rows), 1),
        }

    # ---------- steps ----------
    @mcp.tool(description="Log step count for a day (defaults to today). Overwrites if a record exists.")
    def log_steps(steps: int, day: str | None = None, distance_km: float | None = None, calories: float | None = None) -> dict:
        if steps < 0:
            raise ValueError("steps must be >= 0")
        uid, tz = user_and_tz()
        log_date = parse_date(day) if day else datetime.now(pytz.timezone(tz)).date()
        payload = {
            "user_id": uid, "log_date": log_date.isoformat(), "steps": steps,
            "distance_km": distance_km, "calories": calories,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        return get_supabase().table("step_logs").upsert(payload).execute().data[0]

    @mcp.tool(description="Get today's step count and stats.")
    def get_steps_today() -> dict:
        uid, tz = user_and_tz()
        today = datetime.now(pytz.timezone(tz)).date().isoformat()
        r = get_supabase().table("step_logs").select("*").eq("user_id", uid).eq("log_date", today).maybe_single().execute()
        return (r.data if r else None) or {"log_date": today, "steps": 0}

    @mcp.tool(description="Get step history across a date range.")
    def get_steps_by_date_range(start_date: str, end_date: str) -> dict:
        u = require_user()
        r = get_supabase().table("step_logs").select("*").eq("user_id", u.user_id).gte("log_date", start_date).lte("log_date", end_date).order("log_date").execute()
        rows = r.data or []
        total = sum(x["steps"] for x in rows)
        n = max(len(rows), 1)
        return {"start": start_date, "end": end_date, "days": rows, "total_steps": total, "avg_per_day": round(total / n, 0)}

    # ---------- body metrics ----------
    @mcp.tool(description="Log body metrics: weight, body fat %, circumferences (cm), resting HR.")
    def log_body_metrics(
        weight_kg: float | None = None,
        body_fat_pct: float | None = None,
        waist_cm: float | None = None,
        chest_cm: float | None = None,
        arm_cm: float | None = None,
        thigh_cm: float | None = None,
        resting_hr: int | None = None,
        notes: str | None = None,
        measured_at: str | None = None,
    ) -> dict:
        u = require_user()
        fields = {"weight_kg": weight_kg, "body_fat_pct": body_fat_pct, "waist_cm": waist_cm,
                  "chest_cm": chest_cm, "arm_cm": arm_cm, "thigh_cm": thigh_cm,
                  "resting_hr": resting_hr, "notes": notes}
        if not any(v is not None for v in fields.values()):
            raise ValueError("Provide at least one metric to log.")
        payload = {"user_id": u.user_id, **{k: v for k, v in fields.items() if v is not None}}
        if measured_at:
            payload["measured_at"] = measured_at
        return get_supabase().table("body_metrics").insert(payload).execute().data[0]

    @mcp.tool(description="Body metrics history over the last N days (default 90).")
    def get_body_metrics_history(days: int = 90) -> dict:
        u = require_user()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = get_supabase().table("body_metrics").select("*").eq("user_id", u.user_id).gte("measured_at", cutoff).order("measured_at").execute().data or []
        latest = rows[-1] if rows else None
        first = rows[0] if rows else None
        delta = None
        if latest and first and latest.get("weight_kg") and first.get("weight_kg"):
            delta = round(latest["weight_kg"] - first["weight_kg"], 2)
        return {"days": days, "count": len(rows), "entries": rows, "weight_change_kg": delta}

    # ---------- goals ----------
    @mcp.tool(description="Set fitness goals (any subset of the listed fields).")
    def set_fitness_goals(
        daily_steps: int | None = None,
        weekly_workouts: int | None = None,
        weekly_cardio_min: int | None = None,
        weekly_volume_kg: float | None = None,
        target_weight_kg: float | None = None,
        target_body_fat_pct: float | None = None,
    ) -> dict:
        u = require_user()
        patch = {k: v for k, v in {
            "daily_steps": daily_steps, "weekly_workouts": weekly_workouts,
            "weekly_cardio_min": weekly_cardio_min, "weekly_volume_kg": weekly_volume_kg,
            "target_weight_kg": target_weight_kg, "target_body_fat_pct": target_body_fat_pct,
        }.items() if v is not None}
        patch["user_id"] = u.user_id
        patch["updated_at"] = datetime.now(timezone.utc).isoformat()
        return get_supabase().table("fitness_goals").upsert(patch).execute().data[0]

    @mcp.tool(description="Get current fitness goals.")
    def get_fitness_goals() -> dict:
        u = require_user()
        r = get_supabase().table("fitness_goals").select("*").eq("user_id", u.user_id).maybe_single().execute()
        return (r.data if r else None) or {"user_id": u.user_id}

    @mcp.tool(description="Progress vs fitness goals for the current week + today's steps.")
    def get_fitness_progress() -> dict:
        uid, tz = user_and_tz()
        tzobj = pytz.timezone(tz)
        now_local = datetime.now(tzobj)
        today = now_local.date()
        week_start = today - timedelta(days=today.weekday())              # Monday
        ws, _ = local_day_bounds(tz, week_start)
        _, we = local_day_bounds(tz, today)

        sb = get_supabase()
        goals = (sb.table("fitness_goals").select("*").eq("user_id", uid).maybe_single().execute().data) or {}
        workouts = sb.table("workouts").select("id").eq("user_id", uid).gte("started_at", iso(ws)).lt("started_at", iso(we)).execute().data or []
        sets = sb.table("workout_sets").select("reps,weight_kg,is_warmup").eq("user_id", uid).gte("created_at", iso(ws)).lt("created_at", iso(we)).execute().data or []
        cardio = sb.table("cardio_sessions").select("duration_min").eq("user_id", uid).gte("performed_at", iso(ws)).lt("performed_at", iso(we)).execute().data or []
        steps_today_row = sb.table("step_logs").select("steps").eq("user_id", uid).eq("log_date", today.isoformat()).maybe_single().execute()
        steps_today = ((steps_today_row.data or {}).get("steps") if steps_today_row else None) or 0

        total_volume = round(sum((s.get("reps") or 0) * (s.get("weight_kg") or 0) for s in sets if not s.get("is_warmup")), 1)
        total_cardio = sum((c.get("duration_min") or 0) for c in cardio)

        def _pct(v, g):
            return round(100 * v / g, 1) if g else None

        return {
            "week_start": week_start.isoformat(),
            "today": today.isoformat(),
            "steps_today": {"value": steps_today, "goal": goals.get("daily_steps"), "pct": _pct(steps_today, goals.get("daily_steps"))},
            "workouts_this_week": {"value": len(workouts), "goal": goals.get("weekly_workouts"), "pct": _pct(len(workouts), goals.get("weekly_workouts"))},
            "cardio_min_this_week": {"value": total_cardio, "goal": goals.get("weekly_cardio_min"), "pct": _pct(total_cardio, goals.get("weekly_cardio_min"))},
            "volume_kg_this_week": {"value": total_volume, "goal": goals.get("weekly_volume_kg"), "pct": _pct(total_volume, goals.get("weekly_volume_kg"))},
        }

    # ---------- PRs & progression ----------
    @mcp.tool(description="Full history for a single exercise: every working set, sorted oldest → newest.")
    def get_exercise_history(exercise: str, days: int = 180) -> dict:
        u = require_user()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = get_supabase().table("workout_sets").select("*") \
            .eq("user_id", u.user_id).ilike("exercise_name", exercise) \
            .eq("is_warmup", False).gte("created_at", cutoff) \
            .order("created_at").execute().data or []

        # Best-ever heaviest set + best estimated 1RM
        heaviest = max(rows, key=lambda r: (r.get("weight_kg") or 0), default=None)
        best_e1rm = max(rows, key=lambda r: epley_1rm(r.get("weight_kg") or 0, r.get("reps") or 0), default=None)
        return {
            "exercise": exercise,
            "days": days,
            "set_count": len(rows),
            "sets": rows,
            "heaviest_set": heaviest,
            "best_e1rm_set": {**best_e1rm, "e1rm": epley_1rm(best_e1rm["weight_kg"] or 0, best_e1rm["reps"] or 0)} if best_e1rm else None,
        }

    @mcp.tool(description="Personal records across all exercises: heaviest weight per exercise and best estimated 1RM.")
    def get_personal_records() -> dict:
        u = require_user()
        rows = get_supabase().table("workout_sets").select("exercise_name,reps,weight_kg,created_at") \
            .eq("user_id", u.user_id).eq("is_warmup", False) \
            .order("weight_kg", desc=True).limit(5000).execute().data or []
        best: dict[str, dict] = {}
        for r in rows:
            name = r["exercise_name"]
            w = r.get("weight_kg") or 0
            reps = r.get("reps") or 0
            e1rm = epley_1rm(w, reps)
            cur = best.get(name)
            if not cur or w > cur["heaviest_weight_kg"] or e1rm > cur["best_e1rm"]:
                nw = max(w, cur["heaviest_weight_kg"] if cur else 0)
                ne = max(e1rm, cur["best_e1rm"] if cur else 0)
                best[name] = {
                    "exercise": name,
                    "heaviest_weight_kg": nw,
                    "best_e1rm": ne,
                    "last_updated": r["created_at"],
                }
        return {"records": sorted(best.values(), key=lambda x: x["best_e1rm"], reverse=True)}

    @mcp.tool(description=(
        "Compare recent window vs prior window for volume, sets, workouts, cardio minutes, avg steps. "
        "Surfaces progression deltas."
    ))
    def get_workout_trends(days: int = 14) -> dict:
        if days < 4 or days > 180:
            raise ValueError("days must be between 4 and 180")
        uid, tz = user_and_tz()
        tzobj = pytz.timezone(tz)
        today = datetime.now(tzobj).date()
        half = days // 2
        recent_start = today - timedelta(days=half - 1)
        prior_end = recent_start - timedelta(days=1)
        prior_start = prior_end - timedelta(days=half - 1)
        sb = get_supabase()

        def _window(d1: date, d2: date) -> dict:
            s, _ = local_day_bounds(tz, d1)
            _, e = local_day_bounds(tz, d2)
            wk = sb.table("workouts").select("id").eq("user_id", uid).gte("started_at", iso(s)).lt("started_at", iso(e)).execute().data or []
            sets = sb.table("workout_sets").select("reps,weight_kg,is_warmup").eq("user_id", uid).gte("created_at", iso(s)).lt("created_at", iso(e)).execute().data or []
            cardio = sb.table("cardio_sessions").select("duration_min,distance_km,calories").eq("user_id", uid).gte("performed_at", iso(s)).lt("performed_at", iso(e)).execute().data or []
            steps = sb.table("step_logs").select("steps").eq("user_id", uid).gte("log_date", d1.isoformat()).lte("log_date", d2.isoformat()).execute().data or []
            volume = round(sum((x.get("reps") or 0) * (x.get("weight_kg") or 0) for x in sets if not x.get("is_warmup")), 1)
            return {
                "workouts": len(wk),
                "working_sets": sum(1 for x in sets if not x.get("is_warmup")),
                "volume_kg": volume,
                "cardio_min": sum((c.get("duration_min") or 0) for c in cardio),
                "cardio_km": round(sum((c.get("distance_km") or 0) for c in cardio), 2),
                "avg_steps": round(sum(s["steps"] for s in steps) / max(len(steps), 1), 0),
            }

        recent = _window(recent_start, today)
        prior = _window(prior_start, prior_end)

        def _delta(r, p):
            return {"recent": r, "prior": p, "delta": round(r - p, 2),
                    "pct_change": round((r - p) / p * 100, 1) if p else None}

        return {
            "recent_window": {"start": recent_start.isoformat(), "end": today.isoformat()},
            "prior_window": {"start": prior_start.isoformat(), "end": prior_end.isoformat()},
            "metrics": {k: _delta(recent[k], prior[k]) for k in recent},
        }

    @mcp.tool(description="Break down training by primary muscle group over the last N days (defaults to 30).")
    def get_workout_patterns(days: int = 30) -> dict:
        u = require_user()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        sb = get_supabase()
        sets = sb.table("workout_sets").select("exercise_name,reps,weight_kg,is_warmup,created_at").eq("user_id", u.user_id).gte("created_at", cutoff).execute().data or []
        # Build a lookup from exercise_name → primary_muscle (global + custom)
        exs = sb.table("exercises").select("name,primary_muscle").or_(f"user_id.is.null,user_id.eq.{u.user_id}").execute().data or []
        muscle_by_name = {e["name"].lower(): e.get("primary_muscle") or "other" for e in exs}

        muscle_counts: dict[str, int] = {}
        muscle_volume: dict[str, float] = {}
        ex_counts: dict[str, int] = {}
        for s in sets:
            if s.get("is_warmup"):
                continue
            name = s["exercise_name"]
            muscle = muscle_by_name.get(name.lower(), "other")
            muscle_counts[muscle] = muscle_counts.get(muscle, 0) + 1
            muscle_volume[muscle] = muscle_volume.get(muscle, 0) + (s.get("reps") or 0) * (s.get("weight_kg") or 0)
            ex_counts[name] = ex_counts.get(name, 0) + 1

        return {
            "days": days,
            "total_working_sets": sum(muscle_counts.values()),
            "by_muscle_group": [
                {"muscle": m, "sets": muscle_counts[m], "volume_kg": round(muscle_volume[m], 1)}
                for m in sorted(muscle_counts, key=lambda k: muscle_counts[k], reverse=True)
            ],
            "top_exercises": sorted([{"exercise": k, "sets": v} for k, v in ex_counts.items()], key=lambda x: x["sets"], reverse=True)[:10],
        }

    @mcp.tool(description="Current workout consistency streak: consecutive days (ending today) with at least one workout or >= goal steps.")
    def get_consistency_streak() -> dict:
        uid, tz = user_and_tz()
        tzobj = pytz.timezone(tz)
        today = datetime.now(tzobj).date()
        sb = get_supabase()
        # Look back up to 120 days
        back = today - timedelta(days=120)
        start, _ = local_day_bounds(tz, back)
        _, end = local_day_bounds(tz, today)
        wks = sb.table("workouts").select("started_at").eq("user_id", uid).gte("started_at", iso(start)).lt("started_at", iso(end)).execute().data or []
        steps = sb.table("step_logs").select("log_date,steps").eq("user_id", uid).gte("log_date", back.isoformat()).lte("log_date", today.isoformat()).execute().data or []
        goal_row = sb.table("fitness_goals").select("daily_steps").eq("user_id", uid).maybe_single().execute()
        step_goal = ((goal_row.data or {}).get("daily_steps") if goal_row else None) or 0

        workout_days = {datetime.fromisoformat(w["started_at"].replace("Z", "+00:00")).astimezone(tzobj).date() for w in wks}
        step_days = {date.fromisoformat(s["log_date"]) for s in steps if step_goal and s["steps"] >= step_goal}
        active_days = workout_days | step_days

        streak = 0
        d = today
        while d in active_days:
            streak += 1
            d -= timedelta(days=1)
        return {"current_streak_days": streak, "as_of": today.isoformat(), "step_goal_used": step_goal or None}

    @mcp.tool(description="Rolling 7-day fitness summary: workouts, volume, cardio, steps, body-weight change.")
    def get_fitness_summary(days: int = 7) -> dict:
        if days <= 0 or days > 365:
            raise ValueError("days must be between 1 and 365")
        uid, tz = user_and_tz()
        tzobj = pytz.timezone(tz)
        today = datetime.now(tzobj).date()
        start_day = today - timedelta(days=days - 1)
        start, _ = local_day_bounds(tz, start_day)
        _, end = local_day_bounds(tz, today)
        sb = get_supabase()

        wks = sb.table("workouts").select("id,started_at,duration_min").eq("user_id", uid).gte("started_at", iso(start)).lt("started_at", iso(end)).execute().data or []
        sets = sb.table("workout_sets").select("reps,weight_kg,is_warmup,exercise_name").eq("user_id", uid).gte("created_at", iso(start)).lt("created_at", iso(end)).execute().data or []
        cardio = sb.table("cardio_sessions").select("activity,duration_min,distance_km,calories").eq("user_id", uid).gte("performed_at", iso(start)).lt("performed_at", iso(end)).execute().data or []
        steps = sb.table("step_logs").select("log_date,steps").eq("user_id", uid).gte("log_date", start_day.isoformat()).lte("log_date", today.isoformat()).execute().data or []
        body = sb.table("body_metrics").select("weight_kg,measured_at").eq("user_id", uid).gte("measured_at", iso(start)).lt("measured_at", iso(end)).order("measured_at").execute().data or []

        volume = round(sum((s.get("reps") or 0) * (s.get("weight_kg") or 0) for s in sets if not s.get("is_warmup")), 1)
        return {
            "window": {"start": start_day.isoformat(), "end": today.isoformat(), "days": days},
            "workouts": {"count": len(wks), "avg_duration_min": round(sum((w.get("duration_min") or 0) for w in wks) / max(len(wks), 1), 1)},
            "strength": {
                "working_sets": sum(1 for s in sets if not s.get("is_warmup")),
                "total_volume_kg": volume,
                "unique_exercises": len({s["exercise_name"] for s in sets if not s.get("is_warmup")}),
            },
            "cardio": {
                "sessions": len(cardio),
                "total_min": sum((c.get("duration_min") or 0) for c in cardio),
                "total_km": round(sum((c.get("distance_km") or 0) for c in cardio), 2),
                "total_calories": round(sum((c.get("calories") or 0) for c in cardio), 1),
            },
            "steps": {
                "days_logged": len(steps),
                "total": sum(s["steps"] for s in steps),
                "avg_per_day": round(sum(s["steps"] for s in steps) / max(len(steps), 1), 0),
            },
            "body": {
                "start_weight_kg": body[0]["weight_kg"] if body and body[0].get("weight_kg") else None,
                "end_weight_kg": body[-1]["weight_kg"] if body and body[-1].get("weight_kg") else None,
            },
        }


# ---------------------------------------------------------------------------
# module-private helper (not a tool)
# ---------------------------------------------------------------------------

def _fetch_workouts(uid: str, tz: str, start, end, *, label: str) -> dict:
    sb = get_supabase()
    wks = sb.table("workouts").select("*").eq("user_id", uid).gte("started_at", iso(start)).lt("started_at", iso(end)).order("started_at").execute().data or []
    if not wks:
        return {"range": label, "workouts": [], "count": 0}
    ids = [w["id"] for w in wks]
    sets = sb.table("workout_sets").select("*").in_("workout_id", ids).order("created_at").execute().data or []
    by_workout: dict[str, list] = {}
    for s in sets:
        by_workout.setdefault(s["workout_id"], []).append(s)
    for w in wks:
        w["sets"] = by_workout.get(w["id"], [])
    return {"range": label, "workouts": wks, "count": len(wks)}
