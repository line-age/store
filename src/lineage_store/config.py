"""Settings, from the environment. Nothing here names a deployment (controlplane L-0006)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get(
        "DATABASE_URL", "postgresql://lineage:lineage@localhost:54329/lineage"))
    # People log in with GitHub and must be in one of these teams, "org/team-slug"
    # (controlplane L-0012).
    github_teams: tuple[str, ...] = field(default_factory=lambda: tuple(
        t.strip() for t in os.environ.get("LINEAGE_GITHUB_TEAMS", "").split(",") if t.strip()))
    github_api: str = field(default_factory=lambda: os.environ.get("LINEAGE_GITHUB_API", "https://api.github.com"))
    auth_cache_seconds: int = field(default_factory=lambda: int(os.environ.get("LINEAGE_AUTH_CACHE_SECONDS", "300")))
