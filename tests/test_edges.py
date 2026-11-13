"""What the scenarios don't reach: refusals, the admin command, a reviewer's own cut, and the
view rules on less common shapes (start-date conflicts, chains of merges, several cuts on
one section)."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from lineage_model import began
from test_scenarios import (
    HOBBS,
    JAL,
    MID,
    SHEET_1937,
    journal_claim,
    line,
    removal,
    rows,
    sheet,
    trace,
    valid,
)

from lineage_store import cli

Q1 = (-103.150, 32.556)   # a quarter of the way down
Q3 = (-103.179, 32.260)   # three quarters


def fresh(api):
    s = sheet(api["ann"], 1937, SHEET_1937)
    section, _ = trace(api["ann"], s, line(HOBBS, JAL), as_of=SHEET_1937)
    return s, section


# Splitting ----------------------------------------------------------------------------------

def test_reviewer_draws_the_cut(api, sql):
    _, section = fresh(api)
    _, link = removal(api["ann"], line(MID, JAL), section, relation="overlaps")
    # the reviewer moves the cut: only the last quarter ended
    out = api["bob"].post(f"/links/{link}/reviews", {"verdict": "confirm", "cut": line(Q3, JAL)})
    ended = sql.execute("SELECT ST_Length(geom::geography) AS m FROM qgis.sections WHERE section_id = %s",
                        (out["ended_section_id"],)).fetchone()["m"]
    rest = sql.execute("SELECT ST_Length(geom::geography) AS m FROM qgis.sections WHERE section_id = %s",
                       (out["continuing_section_id"],)).fetchone()["m"]
    assert ended < rest / 2                       # the reviewer's quarter, not the removal's half


def test_a_cut_must_leave_two_pieces(api):
    _, section = fresh(api)
    _, link = removal(api["ann"], line(MID, JAL), section, relation="overlaps")
    r = api["bob"].post(f"/links/{link}/reviews", {"verdict": "confirm", "cut": line(HOBBS, JAL)}, expect=422)
    assert "same_as" in r["detail"]
    assert api["bob"].get(f"/features/{section}")["links"][0]["status"] == "proposed"   # rolled back


# Refusals -----------------------------------------------------------------------------------

def test_claim_review_refusals(api):
    _, section = fresh(api)
    api["ann"].post(f"/claims/{uuid.uuid4()}/reviews", {"verdict": "accept"}, expect=404)
    route = next(c for c in api["ann"].get(f"/features/{section}")["claims"] if c["attribute_key"] == "route")
    api["ann"].post(f"/claims/{route['id']}/reviews", {"verdict": "accept"}, expect=409)       # not a candidate
    api["ann"].post(f"/claims/{route['id']}/reviews", {"verdict": "correct"}, expect=422)      # no replacement
    api["ann"].post(f"/claims/{route['id']}/reviews", {"verdict": "reject"})
    api["ann"].post(f"/claims/{route['id']}/reviews", {"verdict": "reject"}, expect=409)       # already rejected


def test_link_review_refusals(api):
    _, section = fresh(api)
    _, link = removal(api["ann"], line(HOBBS, JAL), section)
    api["ann"].post(f"/links/{uuid.uuid4()}/reviews", {"verdict": "confirm"}, expect=404)
    api["trace"].post(f"/links/{link}/reviews", {"verdict": "confirm"}, expect=403)            # a pipeline
    api["ann"].post(f"/links/{link}/reviews", {"verdict": "reject"})
    api["ann"].post(f"/links/{link}/reviews", {"verdict": "confirm"}, expect=409)              # already decided


def test_bundle_refusals(api):
    s, section = fresh(api)
    cite = [{"citation": "c", "role": "states"}]
    base = {"citations": [{"ref": "c", "source": s}]}
    r = api["ann"].post("/bundles", {**base, "claims": [{"feature": "nope", "attribute": "name", "value_text": "x",
                                                         "citations": cite}]}, expect=422)
    assert "unknown ref 'nope'" in r["detail"]
    r = api["ann"].post("/bundles", {"features": [{"ref": "f", "kind": "line", "network": "gas"},
                                                  {"ref": "f", "kind": "line", "network": "gas"}]}, expect=422)
    assert "used twice" in r["detail"]
    r = api["backport"].post("/bundles", {**base, "claims": [{"feature": section, "attribute": "name", "value_text": "x",
                                                              "asserted_by": str(uuid.uuid4()), "citations": cite}]},
                             expect=422)
    assert "no agent" in r["detail"]
    r = api["trace"].post("/bundles", {"links": [{"from": section, "to": section, "relation": "same_as",
                                                  "status": "confirmed"}]}, expect=422)
    assert "only a person" in r["detail"]
    # an external key names one kind of row
    api["ann"].post("/bundles", {"features": [{"kind": "line", "network": "gas",
                                               "external_key": {"system": "test", "key": "kind-clash"}}]})
    api["ann"].post("/bundles", {"sources": [{"kind": "web", "title": "x",
                                              "external_key": {"system": "test", "key": "kind-clash"}}]}, expect=409)
    # nothing in a refused bundle is kept
    api["ann"].post("/bundles", {**base, "features": [{"ref": "f", "kind": "section", "network": "gas",
                                                       "external_key": {"system": "test", "key": "half-written"}}],
                                 "claims": [{"feature": "f", "attribute": "voltage", "value_num": 1,
                                             "citations": cite}]}, expect=422)
    again = api["ann"].post("/bundles", {"features": [{"ref": "f", "kind": "section", "network": "gas",
                                                       "external_key": {"system": "test", "key": "half-written"}}]})
    assert again["created"]["f"]


def test_agent_conflict(api):
    a = {"system": "test", "key": "tracer:conflict-a"}
    api["backport"].post("/agents", {"kind": "person", "name": "A", "external_key": a})
    api["backport"].post("/agents", {"kind": "person", "name": "B", "github_login": "conflict-b"})
    r = api["backport"].post("/agents", {"kind": "person", "name": "A", "external_key": a, "github_login": "conflict-b"},
                             expect=409)
    assert "two different agents" in r["detail"]


def test_agent_name_follows_the_importer(api, sql):
    k = {"system": "test", "key": "tracer:renamed"}
    first = api["backport"].post("/agents", {"kind": "person", "name": "abc", "external_key": k})
    again = api["backport"].post("/agents", {"kind": "person", "name": "Ann B. Cee", "external_key": k})
    assert again["id"] == first["id"]
    assert sql.execute("SELECT name FROM store.agent WHERE id = %s", (first["id"],)).fetchone()["name"] == "Ann B. Cee"


def test_reads_and_their_refusals(api):
    _, section = fresh(api)
    _, link = removal(api["ann"], line(HOBBS, JAL), section)
    assert link in {str(x["id"]) for x in api["ann"].get("/links", status="proposed", relation="same_as")}
    req = api["ann"].post("/citation-requests", {"feature_id": section})
    assert req["id"] in {str(x["id"]) for x in api["ann"].get("/citation-requests", status="queued")}
    api["ann"].get("/sections", bbox="1,2,3", expect=422)
    api["ann"].get(f"/features/{uuid.uuid4()}", expect=404)
    assert api["ann"].get("/healthz") == {"ok": True}


# The admin command --------------------------------------------------------------------------

def test_cli_tokens_and_roles(database_url, client, sql, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", database_url)
    cli.main(["token", "create", "cli-test", "--pipeline", "cli-test@1"])
    agent_line, tid_line, token = capsys.readouterr().out.strip().splitlines()
    me = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert (me["kind"], me["name"], me["may_assert_for_others"]) == ("pipeline", "cli-test", False)

    cli.main(["token", "create", "cli-test", "--pipeline", "cli-test@2", "--assert-for-others"])
    second = capsys.readouterr().out.strip().splitlines()
    assert second[0] == agent_line                  # same agent, new token
    assert sql.execute("SELECT pipeline FROM store.agent WHERE name = 'cli-test'").fetchone()["pipeline"] == "cli-test@2"

    cli.main(["token", "revoke", tid_line.split()[-1]])
    assert capsys.readouterr().out.strip() == "revoked"
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    cli.main(["token", "revoke", tid_line.split()[-1]])
    assert capsys.readouterr().out.strip() == "no live token with that id"

    cli.main(["role", "map", "qgis_dana", "--github", "dana", "--name", "Dana Example"])
    cli.main(["role", "map", "qgis_dana2", "--github", "dana"])           # a second role, same person
    agents = sql.execute("SELECT DISTINCT agent_id FROM store.agent_role WHERE role IN ('qgis_dana', 'qgis_dana2')").fetchall()
    assert len(agents) == 1


# View rules on less common shapes -----------------------------------------------------------

def test_start_dates_that_disagree(api, sql):
    _, section = fresh(api)                          # on the 1937 sheet
    k = journal_claim(api["cite"], section, "status", "operational", began("1940"))
    api["ann"].post(f"/claims/{k}/reviews", {"verdict": "accept"})
    [r] = rows(sql, section)
    assert (r["date_conflict"], r["time_start"]) == (True, dt.date(1937, 1, 1))   # widened to the earliest


def test_chain_of_merges(api, sql):
    s = sheet(api["ann"], 1937, SHEET_1937)
    a, _ = trace(api["ann"], s, line(HOBBS, JAL), as_of="1931")
    b, _ = trace(api["ann"], s, line(HOBBS, JAL), as_of=SHEET_1937)
    c, _ = trace(api["ann"], s, line(HOBBS, JAL), as_of="1950")
    for frm, to in ((a, b), (b, c)):
        api["ann"].post("/bundles", {"links": [{"from": frm, "to": to, "relation": "same_as", "status": "confirmed"}]})
    assert rows(sql, a) == [] and rows(sql, b) == []
    [r] = rows(sql, c)
    assert r["time_start"] == dt.date(1931, 1, 1)    # a's claim reaches c through b


def test_two_removals_cut_one_section(api, sql):
    _, section = fresh(api)
    removal(api["ann"], line(HOBBS, Q1), section, relation="overlaps")
    removal(api["ann"], line(Q3, JAL), section, relation="overlaps")
    drawn = rows(sql, section)
    assert [r["end_status"] for r in drawn] == [None, "proposed", "proposed"]   # the middle carries on


def test_proposed_links_and_lines_views(api, sql):
    _, section = fresh(api)
    _, link = removal(api["ann"], line(HOBBS, JAL), section)
    pl = sql.execute("SELECT relation, ST_GeometryType(geom) AS t FROM qgis.proposed_links WHERE link_id = %s",
                     (link,)).fetchone()
    assert pl == {"relation": "same_as", "t": "ST_LineString"}
    r = api["ann"].post("/bundles", {"features": [{"ref": "l", "kind": "line", "network": "gas", "label": "Test line"}]})
    assert sql.execute("SELECT label FROM qgis.lines WHERE line_id = %s", (r["ids"]["l"],)).fetchone()["label"] == "Test line"


@pytest.mark.parametrize("value", ["1937", "1937-05", "1937-05-14"])
def test_date_helpers_cover_the_period(value):
    v = valid(began(value))
    assert v["start_earliest"] <= v["start_latest"]
    assert v["start_earliest"].startswith(value[:4])
