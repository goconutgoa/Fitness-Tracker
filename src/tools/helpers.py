"""Shared helpers for tool handlers: user/tz lookup, local-day boundaries,
convenient Supabase query wrappers."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytz

from ..context import require_user
from ..db import get_supabase


def user_and_tz() -> tuple[str, str]:
    """Return (user_id, tz_name). Tz is pulled from app_users; defaults to UTC."""
    u = require_user()
    sb = get_supabase()
    row = sb.table("app_users").select("timezone").eq("id", u.user_id).maybe_single().execute()
    tz = (row.data or {}).get("timezone") if row else None
    return u.user_id, tz or "UTC"


def local_day_bounds(tz_name: str, day: date | None = None) -> tuple[datetime, datetime]:
    tz = pytz.timezone(tz_name)
    now_local = datetime.now(tz)
    target = day or now_local.date()
    start_local = tz.localize(datetime.combine(target, datetime.min.time()))
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def parse_date(s: str) -> date:
    """Accept 'YYYY-MM-DD' or ISO datetime and return a date."""
    if "T" in s:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    return date.fromisoformat(s)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def sum_macros(rows: list[dict]) -> dict[str, float]:
    keys = ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g", "sodium_mg")
    out = {k: 0.0 for k in keys}
    for r in rows:
        for k in keys:
            v = r.get(k)
            if v is not None:
                out[k] += float(v)
    return {k: round(v, 1) for k, v in out.items()}


def require_positive(name: str, value: Any) -> None:
    if value is None or (isinstance(value, (int, float)) and value <= 0):
        raise ValueError(f"{name} must be > 0")
