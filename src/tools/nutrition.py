"""Nutrition MCP tools — mirrors the signatures of the original
akutishevsky/nutrition-mcp so users moving over get the same UX."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

import pytz
from mcp.server.fastmcp import FastMCP

from ..context import require_user
from ..db import get_supabase
from .helpers import iso, local_day_bounds, parse_date, sum_macros, user_and_tz


def register(mcp: FastMCP) -> None:

    # ---------- meals ----------
    @mcp.tool(description="Log a meal with macros. Returns the inserted row.")
    def log_meal(
        name: str,
        calories: float | None = None,
        protein_g: float | None = None,
        carbs_g: float | None = None,
        fat_g: float | None = None,
        fiber_g: float | None = None,
        sugar_g: float | None = None,
        sodium_mg: float | None = None,
        meal_type: Literal["breakfast", "lunch", "dinner", "snack"] | None = None,
        notes: str | None = None,
        consumed_at: str | None = None,
    ) -> dict:
        u = require_user()
        payload = {
            "user_id": u.user_id,
            "name": name,
            "calories": calories, "protein_g": protein_g, "carbs_g": carbs_g,
            "fat_g": fat_g, "fiber_g": fiber_g, "sugar_g": sugar_g, "sodium_mg": sodium_mg,
            "meal_type": meal_type, "notes": notes,
        }
        if consumed_at:
            payload["consumed_at"] = consumed_at
        res = get_supabase().table("meals").insert(payload).execute()
        return res.data[0]

    @mcp.tool(description="Update fields on an existing meal (pass only fields you want to change).")
    def update_meal(
        meal_id: str,
        name: str | None = None,
        calories: float | None = None,
        protein_g: float | None = None,
        carbs_g: float | None = None,
        fat_g: float | None = None,
        fiber_g: float | None = None,
        sugar_g: float | None = None,
        sodium_mg: float | None = None,
        meal_type: str | None = None,
        notes: str | None = None,
        consumed_at: str | None = None,
    ) -> dict:
        u = require_user()
        patch = {k: v for k, v in {
            "name": name, "calories": calories, "protein_g": protein_g, "carbs_g": carbs_g,
            "fat_g": fat_g, "fiber_g": fiber_g, "sugar_g": sugar_g, "sodium_mg": sodium_mg,
            "meal_type": meal_type, "notes": notes, "consumed_at": consumed_at,
        }.items() if v is not None}
        if not patch:
            return {"updated": False, "reason": "no fields provided"}
        res = get_supabase().table("meals").update(patch).eq("id", meal_id).eq("user_id", u.user_id).execute()
        return (res.data or [{}])[0] or {"updated": False}

    @mcp.tool(description="Delete a meal by id.")
    def delete_meal(meal_id: str) -> dict:
        u = require_user()
        get_supabase().table("meals").delete().eq("id", meal_id).eq("user_id", u.user_id).execute()
        return {"deleted": True, "id": meal_id}

    @mcp.tool(description="Get all meals logged today in the user's timezone, with macro totals.")
    def get_meals_today() -> dict:
        uid, tz = user_and_tz()
        start, end = local_day_bounds(tz)
        r = get_supabase().table("meals").select("*").eq("user_id", uid) \
            .gte("consumed_at", iso(start)).lt("consumed_at", iso(end)) \
            .order("consumed_at").execute()
        meals = r.data or []
        return {"date": start.astimezone(pytz.timezone(tz)).date().isoformat(), "meals": meals, "totals": sum_macros(meals), "count": len(meals)}

    @mcp.tool(description="Get meals for a specific local date (YYYY-MM-DD).")
    def get_meals_by_date(day: str) -> dict:
        uid, tz = user_and_tz()
        start, end = local_day_bounds(tz, parse_date(day))
        r = get_supabase().table("meals").select("*").eq("user_id", uid) \
            .gte("consumed_at", iso(start)).lt("consumed_at", iso(end)) \
            .order("consumed_at").execute()
        meals = r.data or []
        return {"date": day, "meals": meals, "totals": sum_macros(meals), "count": len(meals)}

    @mcp.tool(description="Get meals over a date range (inclusive). Returns per-day totals.")
    def get_meals_by_date_range(start_date: str, end_date: str) -> dict:
        uid, tz = user_and_tz()
        start, _ = local_day_bounds(tz, parse_date(start_date))
        _, end = local_day_bounds(tz, parse_date(end_date))
        r = get_supabase().table("meals").select("*").eq("user_id", uid) \
            .gte("consumed_at", iso(start)).lt("consumed_at", iso(end)) \
            .order("consumed_at").execute()
        meals = r.data or []
        by_day: dict[str, list] = {}
        tzobj = pytz.timezone(tz)
        for m in meals:
            d = datetime.fromisoformat(m["consumed_at"].replace("Z", "+00:00")).astimezone(tzobj).date().isoformat()
            by_day.setdefault(d, []).append(m)
        days = [{"date": d, "meals": v, "totals": sum_macros(v)} for d, v in sorted(by_day.items())]
        return {"start_date": start_date, "end_date": end_date, "days": days, "count": len(meals)}

    # ---------- water ----------
    @mcp.tool(description="Log water intake in millilitres.")
    def log_water(amount_ml: int, consumed_at: str | None = None) -> dict:
        u = require_user()
        if amount_ml <= 0:
            raise ValueError("amount_ml must be > 0")
        payload = {"user_id": u.user_id, "amount_ml": amount_ml}
        if consumed_at:
            payload["consumed_at"] = consumed_at
        return get_supabase().table("water_logs").insert(payload).execute().data[0]

    @mcp.tool(description="Delete a water log entry.")
    def delete_water(water_id: str) -> dict:
        u = require_user()
        get_supabase().table("water_logs").delete().eq("id", water_id).eq("user_id", u.user_id).execute()
        return {"deleted": True, "id": water_id}

    @mcp.tool(description="Get today's water intake (total ml + entries).")
    def get_water_today() -> dict:
        uid, tz = user_and_tz()
        start, end = local_day_bounds(tz)
        r = get_supabase().table("water_logs").select("*").eq("user_id", uid) \
            .gte("consumed_at", iso(start)).lt("consumed_at", iso(end)) \
            .order("consumed_at").execute()
        entries = r.data or []
        return {"date": start.astimezone(pytz.timezone(tz)).date().isoformat(),
                "total_ml": sum(e["amount_ml"] for e in entries), "entries": entries}

    @mcp.tool(description="Get water intake for a specific date.")
    def get_water_by_date(day: str) -> dict:
        uid, tz = user_and_tz()
        start, end = local_day_bounds(tz, parse_date(day))
        r = get_supabase().table("water_logs").select("*").eq("user_id", uid) \
            .gte("consumed_at", iso(start)).lt("consumed_at", iso(end)) \
            .order("consumed_at").execute()
        entries = r.data or []
        return {"date": day, "total_ml": sum(e["amount_ml"] for e in entries), "entries": entries}

    # ---------- goals ----------
    @mcp.tool(description="Set daily nutrition targets (any subset of calories, protein_g, carbs_g, fat_g, fiber_g, water_ml).")
    def set_nutrition_goals(
        calories: float | None = None,
        protein_g: float | None = None,
        carbs_g: float | None = None,
        fat_g: float | None = None,
        fiber_g: float | None = None,
        water_ml: int | None = None,
    ) -> dict:
        u = require_user()
        patch = {k: v for k, v in {
            "calories": calories, "protein_g": protein_g, "carbs_g": carbs_g,
            "fat_g": fat_g, "fiber_g": fiber_g, "water_ml": water_ml,
        }.items() if v is not None}
        patch["user_id"] = u.user_id
        patch["updated_at"] = datetime.now(timezone.utc).isoformat()
        return get_supabase().table("nutrition_goals").upsert(patch).execute().data[0]

    @mcp.tool(description="Get current daily nutrition goals.")
    def get_nutrition_goals() -> dict:
        u = require_user()
        r = get_supabase().table("nutrition_goals").select("*").eq("user_id", u.user_id).maybe_single().execute()
        return (r.data if r else None) or {"user_id": u.user_id}

    @mcp.tool(description="Progress vs goals for today: totals, percentages, remaining.")
    def get_goal_progress() -> dict:
        uid, tz = user_and_tz()
        start, end = local_day_bounds(tz)
        sb = get_supabase()
        meals = sb.table("meals").select("*").eq("user_id", uid).gte("consumed_at", iso(start)).lt("consumed_at", iso(end)).execute().data or []
        water = sb.table("water_logs").select("amount_ml").eq("user_id", uid).gte("consumed_at", iso(start)).lt("consumed_at", iso(end)).execute().data or []
        goals = (sb.table("nutrition_goals").select("*").eq("user_id", uid).maybe_single().execute().data) or {}
        totals = sum_macros(meals)
        totals["water_ml"] = sum(w["amount_ml"] for w in water)
        progress = {}
        for k in ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "water_ml"):
            goal = goals.get(k)
            consumed = totals.get(k, 0)
            if goal and goal > 0:
                progress[k] = {"goal": goal, "consumed": consumed, "remaining": round(max(goal - consumed, 0), 1), "pct": round(100 * consumed / goal, 1)}
            else:
                progress[k] = {"goal": None, "consumed": consumed}
        return {"date": start.astimezone(pytz.timezone(tz)).date().isoformat(), "progress": progress}

    # ---------- analytics ----------
    @mcp.tool(description="Rolling N-day nutrition summary (totals + daily averages). Default 7 days.")
    def get_nutrition_summary(days: int = 7) -> dict:
        if days <= 0 or days > 365:
            raise ValueError("days must be between 1 and 365")
        uid, tz = user_and_tz()
        tzobj = pytz.timezone(tz)
        today = datetime.now(tzobj).date()
        start_day = today - timedelta(days=days - 1)
        start, _ = local_day_bounds(tz, start_day)
        _, end = local_day_bounds(tz, today)
        meals = get_supabase().table("meals").select("*").eq("user_id", uid).gte("consumed_at", iso(start)).lt("consumed_at", iso(end)).execute().data or []
        totals = sum_macros(meals)
        avg = {k: round(v / days, 1) for k, v in totals.items()}
        return {"days": days, "start": start_day.isoformat(), "end": today.isoformat(), "totals": totals, "daily_avg": avg, "meal_count": len(meals)}

    @mcp.tool(description="Trend analysis across recent days: compare latest days vs prior window of equal size.")
    def get_trends(days: int = 14) -> dict:
        if days < 4 or days > 90:
            raise ValueError("days must be between 4 and 90")
        uid, tz = user_and_tz()
        tzobj = pytz.timezone(tz)
        today = datetime.now(tzobj).date()
        half = days // 2
        recent_start = today - timedelta(days=half - 1)
        prior_start = recent_start - timedelta(days=half)
        prior_end = recent_start - timedelta(days=1)
        sb = get_supabase()

        def _totals(d1, d2):
            s, _ = local_day_bounds(tz, d1)
            _, e = local_day_bounds(tz, d2)
            rs = sb.table("meals").select("calories,protein_g,carbs_g,fat_g,fiber_g").eq("user_id", uid).gte("consumed_at", iso(s)).lt("consumed_at", iso(e)).execute().data or []
            return sum_macros(rs), len(rs)

        recent, rn = _totals(recent_start, today)
        prior, pn = _totals(prior_start, prior_end)
        deltas = {}
        for k in recent:
            r, p = recent[k], prior[k]
            deltas[k] = {"recent_total": r, "prior_total": p, "delta": round(r - p, 1), "pct_change": round((r - p) / p * 100, 1) if p else None}
        return {"recent_window": {"start": recent_start.isoformat(), "end": today.isoformat(), "meals": rn},
                "prior_window": {"start": prior_start.isoformat(), "end": prior_end.isoformat(), "meals": pn},
                "deltas": deltas}

    @mcp.tool(description="Meal pattern analysis: average intake by day-of-week and meal type over the last N days.")
    def get_meal_patterns(days: int = 30) -> dict:
        if days <= 0 or days > 365:
            raise ValueError("days must be between 1 and 365")
        uid, tz = user_and_tz()
        tzobj = pytz.timezone(tz)
        today = datetime.now(tzobj).date()
        start_day = today - timedelta(days=days - 1)
        start, _ = local_day_bounds(tz, start_day)
        _, end = local_day_bounds(tz, today)
        meals = get_supabase().table("meals").select("*").eq("user_id", uid).gte("consumed_at", iso(start)).lt("consumed_at", iso(end)).execute().data or []

        by_dow: dict[str, list] = {d: [] for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
        by_type: dict[str, list] = {}
        for m in meals:
            dt = datetime.fromisoformat(m["consumed_at"].replace("Z", "+00:00")).astimezone(tzobj)
            by_dow[["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dt.weekday()]].append(m)
            if m.get("meal_type"):
                by_type.setdefault(m["meal_type"], []).append(m)

        return {
            "days_analyzed": days,
            "by_day_of_week": {k: {"count": len(v), "avg_calories": round(sum((x.get("calories") or 0) for x in v) / max(len(v), 1), 1)} for k, v in by_dow.items()},
            "by_meal_type": {k: {"count": len(v), "avg_calories": round(sum((x.get("calories") or 0) for x in v) / max(len(v), 1), 1)} for k, v in by_type.items()},
        }

    # ---------- timezone / account ----------
    @mcp.tool(description="Get current timezone (IANA, e.g. 'America/New_York').")
    def get_timezone() -> dict:
        u = require_user()
        r = get_supabase().table("app_users").select("timezone").eq("id", u.user_id).maybe_single().execute()
        return {"timezone": (r.data or {}).get("timezone", "UTC")}

    @mcp.tool(description="Set your timezone as an IANA name (e.g. 'Asia/Kolkata').")
    def set_timezone(timezone: str) -> dict:
        u = require_user()
        try:
            pytz.timezone(timezone)
        except Exception as exc:
            raise ValueError(f"Invalid IANA timezone: {timezone}") from exc
        get_supabase().table("app_users").update({"timezone": timezone}).eq("id", u.user_id).execute()
        return {"timezone": timezone}

    @mcp.tool(description="Permanently delete your account and all associated data. Irreversible.")
    def delete_account(confirm: str) -> dict:
        if confirm != "DELETE":
            return {"deleted": False, "hint": "Pass confirm='DELETE' to actually delete."}
        u = require_user()
        get_supabase().table("app_users").delete().eq("id", u.user_id).execute()
        return {"deleted": True}
