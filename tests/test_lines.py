"""Lines and their sections: one confirmed line per section, a move and a whole proposed line
decided in one bundle, a line's label from its accepted name, and the local person who
calls with no token. All data is made up (L-0009)."""

from __future__ import annotations

import uuid

from conftest import Api
from test_scenarios import HOBBS, JAL, SHEET_1937, line, sheet, trace

KERMIT = (-103.093, 31.858)


class Local(Api):
    """A caller with no token at all."""

    def __init__(self, client):
        self.c, self.h = client, {}


def section(api):
    s = sheet(api, 1937, SHEET_1937)
    return trace(api, s, line(HOBBS, JAL), as_of=SHEET_1937)[0]


def new_line(api, key=None):
    f = {"ref": "line", "kind": "line", "network": "gas"}
    if key:
        f["external_key"] = {"system": "test", "key": key}
    return str(api.post("/bundles", {"features": [f]})["ids"]["line"])


def links_of(sql, section_id):
    return {str(r["to_feature_id"]): r["status"] for r in sql.execute(
        "SELECT to_feature_id, status FROM store.feature_link WHERE from_feature_id = %s AND relation = 'part_of'",
        (section_id,)).fetchall()}


def link_id(sql, section_id, line_id):
    return str(sql.execute("SELECT id FROM store.feature_link WHERE from_feature_id = %s AND to_feature_id = %s",
                           (section_id, line_id)).fetchone()["id"])


# No token -----------------------------------------------------------------------------------

def test_no_token_is_the_local_person(client, api):
    local = Local(client)
    me = local.get("/me")
    assert me["kind"] == "person" and me["name"] == "Local developer"
    assert local.get("/me")["agent_id"] == me["agent_id"]       # one agent, not one per call
    # the local person decides, as a person does
    s = section(local)
    ln = new_line(local)
    local.post("/bundles", {"links": [{"from": s, "to": ln, "relation": "part_of", "status": "confirmed"}]})
    # a header that is not a bearer token is still refused
    assert client.get("/me", headers={"Authorization": "Basic x"}).status_code == 401


def test_a_pipeline_token_still_cannot_confirm(api):
    s, ln = section(api["ann"]), new_line(api["ann"])
    api["trace"].post("/bundles", {"links": [{"from": s, "to": ln, "relation": "part_of",
                                              "status": "confirmed"}]}, expect=422)
    out = api["trace"].post("/bundles", {"links": [{"ref": "l", "from": s, "to": ln, "relation": "part_of"}]})
    api["trace"].post("/bundles", {"reviews": [{"link": str(out["ids"]["l"]), "verdict": "confirm"}]}, expect=403)


# One line per section -----------------------------------------------------------------------

def test_one_confirmed_line_per_section(api, sql):
    s, a, b = section(api["ann"]), new_line(api["ann"]), new_line(api["ann"])
    api["ann"].post("/bundles", {"links": [{"from": s, "to": a, "relation": "part_of", "status": "confirmed"}]})
    r = api["ann"].c.post("/bundles", headers=api["ann"].h, json={
        "links": [{"from": s, "to": b, "relation": "part_of", "status": "confirmed"}]})
    assert r.status_code == 409 and r.json()["detail"] == "that section is already in a pipeline"
    assert links_of(sql, s) == {a: "confirmed"}


def test_move_is_one_bundle(api, sql):
    s, a, b = section(api["ann"]), new_line(api["ann"]), new_line(api["ann"])
    api["ann"].post("/bundles", {"links": [{"from": s, "to": a, "relation": "part_of", "status": "confirmed"}]})
    api["ann"].post("/bundles", {
        "reviews": [{"link": link_id(sql, s, a), "verdict": "reject", "note": "wrong line"}],
        "links": [{"from": s, "to": b, "relation": "part_of", "status": "confirmed"}]})
    assert links_of(sql, s) == {a: "rejected", b: "confirmed"}
    # taking it out is the reject alone; the section is then in no line
    api["ann"].post("/links/" + link_id(sql, s, b) + "/reviews", {"verdict": "reject"})
    assert links_of(sql, s) == {a: "rejected", b: "rejected"}
    # a rejected membership is not decided again
    api["ann"].post("/links/" + link_id(sql, s, b) + "/reviews", {"verdict": "reject"}, expect=409)


def test_start_a_new_line_from_a_section(api, sql):
    s, a = section(api["ann"]), new_line(api["ann"])
    api["ann"].post("/bundles", {"links": [{"from": s, "to": a, "relation": "part_of", "status": "confirmed"}]})
    out = api["ann"].post("/bundles", {
        "features": [{"ref": "new", "kind": "line", "network": "gas"}],
        "reviews": [{"link": link_id(sql, s, a), "verdict": "reject"}],
        "links": [{"from": s, "to": "new", "relation": "part_of", "status": "confirmed"}]})
    assert links_of(sql, s) == {a: "rejected", str(out["ids"]["new"]): "confirmed"}


def test_whole_proposed_line(api, sql):
    """Three sections proposed into a line; one is already in another. Confirming all three
    fails as a whole (409) and changes nothing; confirming the two unplaced ones works, and
    the third keeps its proposal beside its membership."""
    one, two, placed = section(api["ann"]), section(api["ann"]), section(api["ann"])
    other = new_line(api["ann"])
    api["ann"].post("/bundles", {"links": [{"from": placed, "to": other, "relation": "part_of",
                                            "status": "confirmed"}]})
    out = api["trace"].post("/bundles", {
        "features": [{"ref": "ln", "kind": "line", "network": "gas"}],
        "links": [{"ref": f"l{i}", "from": s, "to": "ln", "relation": "part_of", "score": 0.8}
                  for i, s in enumerate((one, two, placed))]})
    ids = [str(out["ids"][f"l{i}"]) for i in range(3)]
    ln = str(out["ids"]["ln"])
    r = api["ann"].c.post("/bundles", headers=api["ann"].h,
                          json={"reviews": [{"link": i, "verdict": "confirm"} for i in ids]})
    assert r.status_code == 409
    assert links_of(sql, one) == {ln: "proposed"}                 # nothing half done
    api["ann"].post("/bundles", {"reviews": [{"link": i, "verdict": "confirm"} for i in ids[:2]]})
    assert links_of(sql, one) == {ln: "confirmed"} and links_of(sql, two) == {ln: "confirmed"}
    assert links_of(sql, placed) == {other: "confirmed", ln: "proposed"}


def test_bundle_reviews_only_decide_part_of(api, sql):
    s = section(api["ann"])
    twin = section(api["ann"])
    out = api["trace"].post("/bundles", {"links": [{"ref": "l", "from": twin, "to": s, "relation": "same_as",
                                                    "score": 0.9}]})
    api["ann"].post("/bundles", {"reviews": [{"link": str(out["ids"]["l"]), "verdict": "confirm"}]}, expect=422)
    api["ann"].post("/bundles", {"reviews": [{"link": str(uuid.uuid4()), "verdict": "confirm"}]}, expect=404)


def test_the_same_bundle_twice_makes_one_line(api, sql):
    s = section(api["ann"])
    body = {"features": [{"ref": "ln", "kind": "line", "network": "gas",
                          "external_key": {"system": "test", "key": "line:twice"}}],
            "links": [{"ref": "m", "from": s, "to": "ln", "relation": "part_of", "status": "confirmed",
                       "external_key": {"system": "test", "key": "member:twice"}}]}
    first = api["ann"].post("/bundles", body)
    again = api["ann"].post("/bundles", body)
    assert again["ids"] == first["ids"] and not any(again["created"].values())
    assert len(links_of(sql, s)) == 1


# A line's label -----------------------------------------------------------------------------

def test_label_follows_the_accepted_name(api, sql):
    def label(ln):
        return sql.execute("SELECT label FROM store.feature WHERE id = %s", (ln,)).fetchone()["label"]

    j = api["ann"].post("/bundles", {"sources": [{"ref": "j", "kind": "issue", "title": "made-up journal"}],
                                     "citations": [{"ref": "c", "source": "j", "locator": "p. 3"}]})
    cite = str(j["ids"]["c"])
    ln = new_line(api["ann"])
    assert label(ln) is None                                     # no name, no label
    named = api["cite"].post("/bundles", {"claims": [{
        "ref": "n", "feature": ln, "attribute": "name", "value_text": "Example line",
        "start_earliest": "1931-01-01", "citations": [{"citation": cite, "role": "states"}]}]})
    assert label(ln) is None                                     # a candidate is not a name yet
    api["ann"].post(f"/claims/{named['ids']['n']}/reviews", {"verdict": "accept"})
    assert label(ln) == "Example line"
    # renamed later: the newer name is the label, the old one is kept as a claim
    api["ann"].post("/bundles", {"claims": [{
        "feature": ln, "attribute": "name", "value_text": "Example system",
        "start_earliest": "1948-01-01", "citations": [{"citation": cite, "role": "states"}]}]})
    assert label(ln) == "Example system"
