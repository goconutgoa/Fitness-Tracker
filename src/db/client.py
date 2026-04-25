"""Supabase client singleton. Uses the service role key — all access goes through
server-side code, and every query filters by ``user_id`` explicitly. RLS is also
enabled as defense-in-depth (see supabase/schema.sql)."""
from __future__ import annotations

from functools import lru_cache

from supabase import Client, create_client

from ..config import get_settings


@lru_cache
def get_supabase() -> Client:
    s = get_settings()
    return create_client(s.supabase_url, s.supabase_service_key)
