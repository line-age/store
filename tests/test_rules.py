"""The rules the database and API enforce whoever writes: agents, citations, immutability,
the attribute registry, external keys and who may do what."""

from __future__ import annotations

import psycopg
import pytest
from lineage_model import ATTRIBUTES, by
from test_scenarios import HOBBS, JAL, SHEET_1937, line, sheet, trace, valid


def test_registry_matches_migration(sql):
    rows = {r["key"]: r for r in sql.execute("SELECT * FROM store.attribute").fetchall()}
    assert rows.keys() == ATTRIBUTES.keys()
    for key, a in ATTRIBUTES.items():
        r = rows[key]
        assert (r["value_type"], r["cardinality"], tuple(r["feature_kinds"])) == (a.value_type, a.cardinality, a.feature_kinds)
        assert (tuple(r["networks"]) if r["networks"] else None) == a.networks
        assert (tuple(r["enum_values"]) if r["enum_values"] is not None else None) == a.enum_values


def test_commodity_claims_are_refused(api):
    s = sheet(api["ann"], 1937, SHEET_1937)
    section, _ = trace(api["ann"], s, line(HOBBS, JAL), as_of=SHEET_1937)
    r = api["ann"].post("/bundles", {"citations": [{"ref": "c", "source": s}],
                                     "claims": [{"feature": section, "attribute": "commodity", "value_text": "natural_gas",
                                                 "citations": [{"citation": "c", "role": "states"}]}]}, expect=422)
    assert "commodity" in r["detail"]


def test_write_without_agent_is_refused(database_url):
    with psycopg.connect(database_url) as conn, pytest.raises(psycopg.errors.RaiseException, match="no agent"):
        conn.execute("INSERT INTO store.feature (kind, network) VALUES ('section', 'gas')")


def test_direct_role_is_attributed(database_url, sql):
    agent = sql.execute("INSERT INTO store.agent (kind, name, github_login) VALUES ('person', 'QGIS user', 'qgisuser') "
                        "RETURNING id").fetchone()["id"]
    role = sql.execute("SELECT session_user AS r").fetchone()["r"]
    sql.execute("INSERT INTO store.agent_role (role, agent_id) VALUES (%s, %s)", (role, agent))
    try:
        with psycopg.connect(database_url) as conn:
            fid = conn.execute("INSERT INTO store.feature (kind, network) VALUES ('section', 'gas') RETURNING id").fetchone()[0]
        h = sql.execute("SELECT agent_id FROM audit.history WHERE table_name = 'feature' AND new_row->>'id' = %s",
                        (str(fid),)).fetchone()
        assert h["agent_id"] == agent
    finally:
        sql.execute("DELETE FROM store.agent_role WHERE role = %s", (role,))


def test_claim_needs_citation_at_commit(database_url, sql):
    fid = sql.execute("INSERT INTO store.feature (kind, network) VALUES ('section', 'gas') RETURNING id").fetchone()["id"]
    with psycopg.connect(database_url) as conn:
        conn.execute("SELECT set_config('lineage.agent_id', '00000000-0000-0000-0000-000000000001', false)")
        conn.execute("INSERT INTO store.claim (feature_id, attribute_key, value_text, status, asserted_by) "
                     "VALUES (%s, 'name', 'x', 'candidate', '00000000-0000-0000-0000-000000000001')", (fid,))
        with pytest.raises(psycopg.errors.RaiseException, match="no citation"):
            conn.commit()


def test_claims_are_never_edited(api, sql):
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, "1937"), line(HOBBS, JAL), as_of="1937")
    with pytest.raises(psycopg.errors.RaiseException, match="never edited"):
        sql.execute("UPDATE store.claim SET valid_edtf = '1940' WHERE feature_id = %s", (section,))
    with pytest.raises(psycopg.errors.RaiseException, match="never deleted"):
        sql.execute("DELETE FROM store.claim WHERE feature_id = %s", (section,))


def test_value_must_fit_attribute(api):
    s = sheet(api["ann"], 1937, "1937")
    section, _ = trace(api["ann"], s, line(HOBBS, JAL), as_of="1937")
    bad = {"citations": [{"ref": "c", "source": s}], "claims": [
        {"feature": section, "attribute": "status", "value_text": "abandoned-ish", "citations": [{"citation": "c", "role": "states"}]}]}
    assert "not a value of status" in api["ann"].post("/bundles", bad, expect=422)["detail"]
    volts = {"citations": [{"ref": "c", "source": s}], "claims": [
        {"feature": section, "attribute": "voltage", "value_num": 69, "value_unit": "kV",
         "citations": [{"citation": "c", "role": "states"}]}]}
    assert "the gas network" in api["ann"].post("/bundles", volts, expect=422)["detail"]


def test_machine_claims_start_as_candidates(api):
    s = sheet(api["ann"], 1937, "1937")
    section, _ = trace(api["ann"], s, line(HOBBS, JAL), as_of="1937")
    body = {"citations": [{"ref": "c", "source": s}], "claims": [
        {"feature": section, "attribute": "diameter", "value_num": 10, "value_unit": "in", "status": "accepted",
         "citations": [{"citation": "c", "role": "states"}]}]}
    api["cite"].post("/bundles", body, expect=422)
    # a pipeline can't review, or confirm a link, either
    body["claims"][0].pop("status")
    claim = api["cite"].post("/bundles", body)["ids"]["claims[0]"]
    api["cite"].post(f"/claims/{claim}/reviews", {"verdict": "accept"}, expect=403)


def test_assert_for_others_needs_permission(api, tokens):
    tracer = api["backport"].post("/agents", {"kind": "person", "name": "Tracer JD",
                                              "external_key": {"system": "test", "key": "tracer:jd"}})["id"]
    s = sheet(api["ann"], 1937, "1937")
    _, _r = trace(api["backport"], s, line(HOBBS, JAL), as_of="1937", asserted_by=tracer)
    api["cite"].post("/agents", {"kind": "person", "name": "x"}, expect=403)
    body = {"citations": [{"ref": "c", "source": s}],
            "features": [{"ref": "f", "kind": "section", "network": "gas"}],
            "claims": [{"feature": "f", "attribute": "route", "value_geom": line(HOBBS, JAL), "asserted_by": tracer,
                        "citations": [{"citation": "c", "role": "map_shows"}]}]}
    api["cite"].post("/bundles", body, expect=403)


def test_tracer_code_later_mapped_to_github(api, client):
    key = {"system": "test", "key": "tracer:cl"}
    first = api["backport"].post("/agents", {"kind": "person", "name": "Tracer CL", "external_key": key})
    again = api["backport"].post("/agents", {"kind": "person", "name": "Tracer CL", "external_key": key,
                                             "github_login": "cal"})
    assert again["id"] == first["id"] and not again["created"]
    # cal logs in with GitHub and is that same agent, not a second one
    me = client.get("/me", headers={"Authorization": "Bearer gh-cal"}).json()
    assert me["agent_id"] == str(first["id"])


def test_github_login_and_team_gate(api, client):
    me = api["ann"].get("/me")
    assert me["kind"] == "person" and me["name"] == "Ann Example"
    assert client.get("/me", headers={"Authorization": "Bearer gh-eve"}).status_code == 403
    assert client.get("/me", headers={"Authorization": "Bearer lgt_nope"}).status_code == 401
    assert client.get("/me").status_code == 401


def test_external_keys_make_bundles_idempotent(api):
    s = sheet(api["ann"], 1937, "1937")
    body = {"citations": [{"ref": "c", "source": s, "external_key": {"system": "test", "key": "c:idem"}}],
            "features": [{"ref": "f", "kind": "section", "network": "gas", "external_key": {"system": "test", "key": "f:idem"}}],
            "claims": [{"ref": "r", "feature": "f", "attribute": "route", "value_geom": line(HOBBS, JAL), **valid(by("1937")),
                        "citations": [{"citation": "c", "role": "map_shows"}], "external_key": {"system": "test", "key": "r:idem"}}]}
    one, two = api["ann"].post("/bundles", body), api["ann"].post("/bundles", body)
    assert one["ids"] == two["ids"]
    assert all(one["created"].values()) and not any(two["created"].values())


def test_citation_request_queue(api):
    s = sheet(api["ann"], 1937, "1937")
    a, _ = trace(api["ann"], s, line(HOBBS, JAL), as_of="1937")
    b, _ = trace(api["ann"], s, line(HOBBS, JAL), as_of="1937")
    while api["cite"].c.post("/citation-requests/next", headers=api["cite"].h).status_code == 200:
        pass                                     # drain anything earlier tests left queued
    bg = api["backport"].post("/citation-requests", {"feature_id": a, "attribute_keys": ["diameter"]})
    fg = api["ann"].post("/citation-requests", {"feature_id": b, "priority": "interactive"})
    assert api["cite"].post("/citation-requests/next")["request"]["id"] == fg["id"]   # interactive first
    assert api["cite"].post("/citation-requests/next")["request"]["id"] == bg["id"]
    assert api["cite"].post("/citation-requests/next", expect=204) is None
    api["ann"].post("/citation-requests/next", expect=403)
    api["cite"].post(f"/citation-requests/{bg['id']}/complete", {"status": "failed", "error": "corpus down"})
    api["cite"].post(f"/citation-requests/{bg['id']}/complete", {"status": "done"}, expect=409)


def test_missing_and_reads(api):
    s = sheet(api["ann"], 1937, "1937")
    section, _ = trace(api["ann"], s, line(HOBBS, JAL), as_of="1937")
    assert section in {str(m["section_id"]) for m in api["ann"].get("/missing", attribute="diameter", network="gas")}
    fc = api["ann"].get("/sections", bbox="-104,32,-103,33", at="1940-01-01")
    assert section in {f["properties"]["section_id"] for f in fc["features"]}
    assert section not in {f["properties"]["section_id"] for f in api["ann"].get("/sections", at="1930-01-01")["features"]}
