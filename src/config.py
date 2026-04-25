"""Runtime configuration loaded from environment variables."""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class Settings(BaseModel):
    supabase_url: str
    supabase_service_key: str
    public_base_url: str
    jwt_secret: str
    session_secret: str
    admin_emails: list[str] = []
    port: int = 8080

    @property
    def issuer(self) -> str:
        return self.public_base_url.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings(
        supabase_url=os.environ["SUPABASE_URL"],
        supabase_service_key=os.environ["SUPABASE_SERVICE_KEY"],
        public_base_url=os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080"),
        jwt_secret=os.environ["JWT_SECRET"],
        session_secret=os.environ["SESSION_SECRET"],
        admin_emails=[e.strip() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()],
        port=int(os.environ.get("PORT", "8080")),
    )
