"""Tests run against a real PostGIS: a fresh database per run on the server named by
LINEAGE_TEST_ADMIN_URL (default: the dev container from compose.yaml), migrated with dbmate.
All fixture data here is made up (controlplane L-0009)."""

from __future__ import annotations

import os
import subprocess
import uuid

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from lineage_store.app import create_app
from lineage_store.auth import Authenticator, new_token
from lineage_store.config import Settings
from lineage_store.db import STORE_AGENT

ADMIN_URL = os.environ.get("LINEAGE_TEST_ADMIN_URL", "postgresql://lineage:lineage@localhost:47119/lineage")
HERE = os.path.dirname(__file__)
MIGRATIONS = os.path.join(HERE, "..", "db", "migrations")


@pytest.fixture(scope="session")
def database_url():
    name = f"lineage_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = ADMIN_URL.rsplit("/", 1)[0] + "/" + name
    subprocess.run(["dbmate", "--url", url + "?sslmode=disable", "--migrations-dir", MIGRATIONS,
                    "--no-dump-schema", "up"], check=True, capture_output=True)
    yield url
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def sql(database_url):
    """A direct connection, as the store's own agent, for setup and for checking rows."""
    conn = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
    conn.execute("SELECT set_config('lineage.agent_id', %s, false)", (str(STORE_AGENT),))
    yield conn
    conn.close()


# A fake GitHub: tokens "gh-<login>"; ann, bob and cal are in line-age/team-store, eve isn't.
MEMBERS = {"ann": "Ann Example", "bob": "Bob Example", "cal": "Cal Example", "eve": "Eve Outsider"}
TEAM = {"ann", "bob", "cal"}


def fake_github(request: httpx.Request) -> httpx.Response:
    token = request.headers["authorization"].removeprefix("Bearer ")
    login = token.removeprefix("gh-")
    if not token.startswith("gh-") or login not in MEMBERS:
        return httpx.Response(401)
    if request.url.path == "/user":
        return httpx.Response(200, json={"login": login, "name": MEMBERS[login],
                                         "email": f"{login}@example.org" if login == "ann" else None})
    if request.url.path == f"/orgs/line-age/teams/team-store/memberships/{login}":
        return httpx.Response(200, json={"state": "active"}) if login in TEAM else httpx.Response(404)
    return httpx.Response(404)


@pytest.fixture(scope="session")
def client(database_url):
    settings = Settings(database_url=database_url, github_teams=("line-age/team-store",),
                        github_api="https://github.test", auth_cache_seconds=0)
    auth = Authenticator(None, settings.github_teams, settings.github_api, 0,
                         http=httpx.Client(transport=httpx.MockTransport(fake_github)))
    with TestClient(create_app(settings, auth)) as c:
        yield c


def _pipeline_token(sql, name: str, assert_for_others: bool = False) -> str:
    row = sql.execute("SELECT id FROM store.agent WHERE kind = 'pipeline' AND name = %s", (name,)).fetchone()
    agent = row["id"] if row else sql.execute(
        "INSERT INTO store.agent (kind, name, pipeline) VALUES ('pipeline', %s, %s) RETURNING id",
        (name, name + "@test")).fetchone()["id"]
    token, digest = new_token()
    sql.execute("INSERT INTO store.agent_token (agent_id, token_sha256, may_assert_for_others) VALUES (%s, %s, %s)",
                (agent, digest, assert_for_others))
    return token


@pytest.fixture(scope="session")
def tokens(sql):
    return {
        "ann": "gh-ann",                     # a person in the team
        "bob": "gh-bob",
        "eve": "gh-eve",                     # a GitHub user outside the team
        "cite": _pipeline_token(sql, "cite"),
        "trace": _pipeline_token(sql, "trace"),
        "backport": _pipeline_token(sql, "backport", assert_for_others=True),
    }


class Api:
    def __init__(self, client: TestClient, token: str):
        self.c, self.h = client, {"Authorization": f"Bearer {token}"}

    def post(self, path, body=None, expect=200):
        r = self.c.post(path, json=body, headers=self.h)
        assert r.status_code == expect, r.text
        return r.json() if r.content else None

    def get(self, path, expect=200, **params):
        r = self.c.get(path, params=params, headers=self.h)
        assert r.status_code == expect, r.text
        return r.json()


@pytest.fixture(scope="session")
def api(client, tokens):
    return {name: Api(client, tok) for name, tok in tokens.items()}
