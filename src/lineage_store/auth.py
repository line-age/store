"""Who is calling.

Every caller is an agent (docs/model.md, `AGENT`):

- **Pipelines** (`cite`, `backport`, `trace`, bulk jobs) send a token made with
  `lineage-store token create`. Tokens start with `lgt_`; only their hash is stored, and a
  token can be revoked.
- **People** send a GitHub token (`review` does the OAuth in the browser). The store asks
  GitHub who it is and whether they are in one of `LINEAGE_GITHUB_TEAMS` (controlplane
  L-0012), and finds or creates their person agent by GitHub login.

People writing straight to PostGIS from QGIS are named by `store.agent_role` instead; that
happens in the database, not here.
"""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass

import httpx
from fastapi import HTTPException, Request

from .db import STORE_AGENT, Database

TOKEN_PREFIX = "lgt_"


@dataclass(frozen=True)
class Principal:
    agent_id: uuid.UUID
    kind: str  # person | pipeline
    name: str
    may_assert_for_others: bool = False


def new_token() -> tuple[str, str]:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, sha256(token)


def sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Authenticator:
    def __init__(self, db: Database, teams: tuple[str, ...], github_api: str, cache_seconds: int,
                 http: httpx.Client | None = None):
        self.db = db
        self.teams = teams
        self.github_api = github_api.rstrip("/")
        self.cache_seconds = cache_seconds
        self.http = http or httpx.Client(timeout=10)
        self._cache: dict[str, tuple[float, Principal]] = {}

    def __call__(self, request: Request) -> Principal:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(401, "send a bearer token: a pipeline token or a GitHub token")
        token = header[7:].strip()
        key = sha256(token)
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        principal = self._pipeline(key) if token.startswith(TOKEN_PREFIX) else self._person(token)
        self._cache[key] = (time.monotonic() + self.cache_seconds, principal)
        return principal

    def _pipeline(self, key: str) -> Principal:
        with self.db.read() as conn:
            row = conn.execute(
                """SELECT a.id, a.kind, a.name, t.may_assert_for_others
                   FROM store.agent_token t JOIN store.agent a ON a.id = t.agent_id
                   WHERE t.token_sha256 = %s AND t.revoked_at IS NULL""", (key,)).fetchone()
        if not row:
            raise HTTPException(401, "unknown or revoked token")
        return Principal(row["id"], row["kind"], row["name"], row["may_assert_for_others"])

    def _person(self, token: str) -> Principal:
        if not self.teams:
            raise HTTPException(401, "GitHub login is not configured (LINEAGE_GITHUB_TEAMS)")
        auth = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        user = self.http.get(f"{self.github_api}/user", headers=auth)
        if user.status_code != 200:
            raise HTTPException(401, "GitHub did not accept that token")
        login = user.json()["login"]
        name = user.json().get("name") or login
        if not any(self._in_team(auth, team, login) for team in self.teams):
            raise HTTPException(403, f"{login} is not in any of: {', '.join(self.teams)}")
        with self.db.write(STORE_AGENT) as conn:
            row = conn.execute(
                "SELECT id, kind, name FROM store.agent WHERE github_login = %s", (login,)).fetchone()
            if not row:
                row = conn.execute(
                    """INSERT INTO store.agent (kind, name, github_login) VALUES ('person', %s, %s)
                       RETURNING id, kind, name""", (name, login)).fetchone()
        if row["kind"] != "person":
            raise HTTPException(403, f"{login} is registered as a pipeline")
        return Principal(row["id"], "person", row["name"])

    def _in_team(self, auth: dict, team: str, login: str) -> bool:
        org, _, slug = team.partition("/")
        r = self.http.get(f"{self.github_api}/orgs/{org}/teams/{slug}/memberships/{login}", headers=auth)
        return r.status_code == 200 and r.json().get("state") == "active"
