"""The light reads: what `/sections` says about each section beside its line, several features
at once, and links of one network. All data here is made up."""

from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from typing import Any

import pytest
from lineage_model import began, began_between, by
from pydantic import BaseModel
from test_scenarios import (
    HOBBS,
    JAL,
    MID,
    SHEET_1937,
    SHEET_1955,
    SHEET_1960,
    journal_claim,
    line,
    removal,
    sheet,
    valid,
)
from test_scenarios import trace as trace_from

WEST = (-104.0, 33.0), (-104.1, 33.4)


class AcceptedClaim(BaseModel):
    """One accepted claim with at least one citation, raw: a client words the value."""
    claim_id: uuid.UUID
    value_text: str | None
    value_num: float | None
    value_unit: str | None
    valid_edtf: str | None
    created_at: dt.datetime
    shown_as_of: str | None        # the earliest map sheet cited as showing it, as the sheet dates it
    inherited_from: uuid.UUID | None   # set when the claim is another feature's, through a confirmed link


class Tracer(BaseModel):
    id: uuid.UUID
    name: str


class Summary(BaseModel):
    """What `/sections?include=summary` adds to each row: the contract Studio relies on."""
    accepted: dict[str, list[AcceptedClaim]]
    candidates: dict[str, int]
    shown_years: list[int]
    gone_year: int | None
    created_at: dt.datetime
    whole_route: dict[str, Any] | None
    traced_by: Tracer | None       # the person who asserted its current route; never a tool


def traced(api, ends=(HOBBS, JAL), year=1937, as_of=SHEET_1937, network="gas"):
    """A section traced from the map sheet of a year."""
    section, _ = trace_from(api["ann"], sheet(api["ann"], year, as_of), line(*ends), as_of=as_of, network=network)
    return section


def inches(api, section, n, when):
    """A diameter a journal states, in inches."""
    return journal_claim(api, section, "diameter", None, when, value_num=n, value_unit="in")


def rows(api, section):
    """The `/sections` rows of a section, with their summaries, checked against the shape."""
    features = api.get("/sections", network="gas", limit=10000, include="summary")["features"]
    mine = [f for f in features if f["properties"]["section_id"] == section]
    for f in mine:
        Summary.model_validate(f["properties"])
    return mine


def props(api, section):
    [f] = [f for f in rows(api, section) if f["properties"]["row_id"] == section]
    return f["properties"]


def test_accepted_newest_first_candidates_counted_rejected_left_out(api):
    section = traced(api)
    old = inches(api["ann"], section, 8, by("1937"))
    new = inches(api["ann"], section, 10, by("1950"))
    inches(api["cite"], section, 12, by("1951"))                                   # a candidate
    wrong = journal_claim(api["cite"], section, "operator", "Made-up Gas Co.", by("1950"))
    api["ann"].post(f"/claims/{wrong}/reviews", {"verdict": "reject"})

    p = props(api["ann"], section)
    assert [c["claim_id"] for c in p["accepted"]["diameter"]] == [new, old]        # newest first
    assert (p["accepted"]["diameter"][0]["value_num"], p["accepted"]["diameter"][0]["value_unit"]) == (10, "in")
    assert p["candidates"] == {"diameter": 1}
    assert "operator" not in p["accepted"] and "operator" not in p["candidates"]
    # the route is there as a fact without its geometry; status with the sheet's date
    assert set(p["accepted"]) == {"route", "status", "diameter"}
    assert "value_geom" not in p["accepted"]["route"][0]
    assert p["accepted"]["status"][0]["shown_as_of"] == SHEET_1937
    assert p["shown_years"] == [1937] and p["gone_year"] is None and p["created_at"]


def test_accepted_without_citation_left_out(api, sql):
    section = traced(api)
    k = inches(api["ann"], section, 8, by("1937"))
    sql.execute("ALTER TABLE store.claim_citation DISABLE TRIGGER USER")
    try:
        sql.execute("DELETE FROM store.claim_citation WHERE claim_id = %s", (k,))
    finally:
        sql.execute("ALTER TABLE store.claim_citation ENABLE TRIGGER USER")
    assert "diameter" not in props(api["ann"], section)["accepted"]


def test_sheet_dated_by_month_and_no_sheet(api):
    section = traced(api, WEST, 1929, "1929-08")
    p = props(api["ann"], section)
    assert p["shown_years"] == [1929] and p["accepted"]["status"][0]["shown_as_of"] == "1929-08"

    # traced from a journal, not a map: no sheet, no years
    r = api["ann"].post("/bundles", {
        "sources": [{"ref": "j", "kind": "issue", "title": "Made-up Journal"}],
        "citations": [{"ref": "c", "source": "j", "locator": "p. 3"}],
        "features": [{"ref": "f", "kind": "section", "network": "gas"}],
        "claims": [{"feature": "f", "attribute": "route", "value_geom": line(*WEST),
                    "citations": [{"citation": "c", "role": "states"}]}]})
    p = props(api["ann"], str(r["ids"]["f"]))
    assert p["shown_years"] == [] and p["accepted"]["route"][0]["shown_as_of"] is None


def test_confirmed_removal_is_history_not_a_merge(api):
    section = traced(api)
    rm, link = removal(api["ann"], line(HOBBS, JAL), section)
    assert props(api["ann"], section)["gone_year"] is None          # only proposed so far
    api["bob"].post(f"/links/{link}/reviews", {"verdict": "confirm"})

    p = props(api["ann"], section)
    assert p["gone_year"] == 1960
    statuses = {c["value_text"]: c["inherited_from"] for c in p["accepted"]["status"]}
    assert statuses == {"operational": None, "decommissioned": rm}

    [f] = api["ann"].get("/features", ids=section)
    assert not [c for c in f["claims"] if c["value_text"] == "decommissioned"]     # not copied
    inherited = [c for c in f["inherited_claims"] if c["attribute_key"] == "status"]
    assert [(c["value_text"], c["inherited_from"]) for c in inherited] == [("decommissioned", rm)]
    assert inherited[0]["citations"][0]["role"] == "map_omits"
    assert f["dates"]["time_end"] == "1960-12-31"


def test_cut_piece_rows(api):
    section = traced(api)
    journal_claim(api["ann"], section, "status", "operational", began("1931-05"))
    _, link = removal(api["ann"], line(MID, JAL), section, relation="overlaps", score=0.98)
    drawn = {f["properties"]["row_id"]: f["properties"] for f in rows(api["ann"], section)}
    assert set(drawn) == {section, link}
    whole, piece = drawn[section], drawn[link]
    # every row of the section carries its route as traced, which no row's geometry is
    assert whole["whole_route"]["coordinates"] == [list(HOBBS), list(JAL)]
    assert piece["whole_route"] == whole["whole_route"]
    assert props(api["ann"], traced(api, WEST))["whole_route"] is None
    assert whole["accepted"] == piece["accepted"] and piece["section_id"] == section


def test_features_by_ids(api):
    a, b = traced(api), traced(api, WEST, 1955, "1955-06-30")
    missing = str(uuid.uuid4())
    got = api["ann"].get("/features", ids=f"{b},{missing},{a}")
    assert [f["id"] for f in got] == [b, a]                          # in order, the unknown one left out
    assert [f["id"] for f in api["ann"].get("/features", ids=f" {b}, {a} ")] == [b, a]
    assert got[1] == {**api["ann"].get(f"/features/{a}")}            # one at a time says the same
    assert got[1]["inherited_claims"] == []
    api["ann"].get("/features", expect=422, ids=",".join(str(uuid.uuid4()) for _ in range(201)))
    api["ann"].get("/features", expect=422, ids="not-an-id")
    api["ann"].get(f"/features/{missing}", expect=404)


def test_links_of_one_network(api):
    section = traced(api, ((-90.0, 40.0), (-90.1, 40.2)), network="water")
    _, link = removal(api["ann"], line((-90.0, 40.0), (-90.1, 40.2)), section)
    water = api["ann"].get("/links", network="water", relation="same_as")
    assert link in {x["id"] for x in water}
    assert all(x["id"] != link for x in api["ann"].get("/links", network="oil"))
    mine = next(x for x in water if x["id"] == link)
    assert (mine["asserted_by_name"], mine["asserted_by_kind"]) == ("Ann Example", "person")


def test_sections_compressed(client, tokens):
    r = client.get("/sections", params={"limit": 10000}, headers={"Authorization": f"Bearer {tokens['ann']}",
                                                                   "Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"


def test_shown_as_of_is_the_earliest_sheet_by_year(api):
    """`~1931` and `../1931` sort after `1937` and before `1928` as text; by year they don't."""
    s37, s31 = sheet(api["ann"], 1937, SHEET_1937), sheet(api["ann"], 1931, "~1931")
    s28, s30 = sheet(api["ann"], 1928, "1928"), sheet(api["ann"], 1930, "../1931")
    section = traced(api, WEST)

    def shown_by(*sheets, attribute):
        api["ann"].post("/bundles", {
            "citations": [{"ref": f"c{i}", "source": s} for i, s in enumerate(sheets)],
            "claims": [{"feature": section, "attribute": attribute, "value_text": "Made-up Gas Co.",
                        **valid(by(SHEET_1937)),
                        "citations": [{"citation": f"c{i}", "role": "map_shows"} for i in range(len(sheets))]}]})

    shown_by(s37, s31, attribute="owner")
    shown_by(s30, s28, attribute="operator")
    p = props(api["ann"], section)
    assert p["accepted"]["owner"][0]["shown_as_of"] == "~1931"
    assert p["accepted"]["operator"][0]["shown_as_of"] == "1928"


def test_removal_without_route_cuts_nothing(api, sql):
    """A proposed `overlaps` from a removal with no route: QGIS draws the section whole, so
    its row carries no whole route either."""
    section = traced(api)
    s60 = sheet(api["ann"], 1960, SHEET_1960)
    api["ann"].post("/bundles", {
        "citations": [{"ref": "c", "source": s60}],
        "features": [{"ref": "rm", "kind": "section", "network": "gas"}],
        "claims": [{"feature": "rm", "attribute": "status", "value_text": "decommissioned",
                    **valid(began_between(SHEET_1955, SHEET_1960)),
                    "citations": [{"citation": "c", "role": "map_omits"}]}],
        "links": [{"from": "rm", "to": section, "relation": "overlaps", "score": 0.9}]})
    [r] = rows(api["ann"], section)
    assert r["properties"]["whole_route"] is None
    assert len(sql.execute("SELECT 1 FROM qgis.sections WHERE section_id = %s", (section,)).fetchall()) == 1


def test_pieces_of_a_split_inherit_the_parent(api):
    """A confirmed cut splits the section; each piece has the parent's claims through
    `split_into`, in the row and in the feature, marked with where they come from."""
    section = traced(api)
    owner = journal_claim(api["ann"], section, "owner", "Made-up Gas Co.", by("1937"))
    _, link = removal(api["ann"], line(MID, JAL), section, relation="overlaps", score=0.98)
    out = api["bob"].post(f"/links/{link}/reviews", {"verdict": "confirm"})
    rest = str(out["continuing_section_id"])

    p = props(api["ann"], rest)
    assert [(c["claim_id"], c["inherited_from"]) for c in p["accepted"]["owner"]] == [(owner, section)]

    [f] = api["ann"].get("/features", ids=rest)
    mine = [c for c in f["inherited_claims"] if c["attribute_key"] == "owner"]
    assert [(c["id"], c["inherited_from"]) for c in mine] == [(owner, section)]
    keys = [(c["inherited_from"], c["attribute_key"], c["created_at"]) for c in f["inherited_claims"]]
    assert keys == sorted(keys)                                     # stable: by feature, attribute, time
    assert {x["asserted_by_kind"] for x in f["links"]} == {"person"}


def test_scope_and_dates_for_one_section_match_the_views(api, sql):
    """Asking the scope and date functions for one section gives that section's rows of the
    views QGIS reads."""
    section = traced(api)
    _, link = removal(api["ann"], line(HOBBS, JAL), section)
    api["bob"].post(f"/links/{link}/reviews", {"verdict": "confirm"})     # dates through scope

    one = sql.execute("SELECT * FROM store.section_dates_for(ARRAY[%s::uuid])", (section,)).fetchall()
    assert one == sql.execute("SELECT * FROM store.section_dates WHERE section_id = %s", (section,)).fetchall()
    assert str(one[0]["time_end"]) == "1960-12-31"
    scope = sql.execute("SELECT * FROM store.claim_scope_for(ARRAY[%s::uuid]) ORDER BY 2", (section,)).fetchall()
    assert scope == sql.execute("SELECT * FROM store.claim_scope WHERE section_id = %s ORDER BY 2",
                                (section,)).fetchall() and len(scope) == 2


def test_summary_only_when_asked(api):
    section = traced(api)
    [plain] = [f for f in api["ann"].get("/sections", network="gas", limit=10000)["features"]
               if f["properties"]["section_id"] == section]
    assert not set(Summary.model_fields) & set(plain["properties"])
    api["ann"].get("/sections", expect=422, include="everything")
    api["ann"].get("/sections", expect=422, bbox="1,2,3")


def test_traced_by_is_the_person_who_asserted_the_route(api, sql):
    section = traced(api)
    ann = sql.execute("SELECT id FROM store.agent WHERE github_login = 'ann'").fetchone()["id"]
    assert props(api["ann"], section)["traced_by"] == {"id": str(ann), "name": "Ann Example"}
    # a later claim by someone else, or by a tool, does not change who traced it
    inches(api["cite"], section, 12, by("1951"))
    inches(api["bob"], section, 10, by("1950"))
    assert props(api["ann"], section)["traced_by"]["name"] == "Ann Example"
    # a route a tool asserted names nobody
    tool = sql.execute("SELECT id FROM store.agent WHERE kind = 'pipeline' LIMIT 1").fetchone()["id"]
    sql.execute("ALTER TABLE store.claim DISABLE TRIGGER USER")
    try:
        sql.execute("UPDATE store.claim SET asserted_by = %s WHERE feature_id = %s AND attribute_key = 'route'",
                    (tool, section))
    finally:
        sql.execute("ALTER TABLE store.claim ENABLE TRIGGER USER")
    assert props(api["ann"], section)["traced_by"] is None


def test_section_with_only_an_uncited_route(api, sql):
    """Nothing cited: the row is there, saying nothing."""
    section = traced(api)
    sql.execute("ALTER TABLE store.claim_citation DISABLE TRIGGER USER")
    try:
        sql.execute("DELETE FROM store.claim_citation WHERE claim_id IN "
                    "(SELECT id FROM store.claim WHERE feature_id = %s)", (section,))
    finally:
        sql.execute("ALTER TABLE store.claim_citation ENABLE TRIGGER USER")
    p = props(api["ann"], section)
    assert (p["accepted"], p["candidates"], p["shown_years"], p["gone_year"]) == ({}, {}, [], None)


def test_two_routes_written_together_agree_everywhere(api, sql):
    """Claims of one bundle share `created_at`; the id settles which route is current, the
    same for the drawn line and for the years it is shown."""
    s37, s55 = sheet(api["ann"], 1937, SHEET_1937), sheet(api["ann"], 1955, SHEET_1955)
    r = api["ann"].post("/bundles", {
        "citations": [{"ref": "c37", "source": s37}, {"ref": "c55", "source": s55}],
        "features": [{"ref": "f", "kind": "section", "network": "gas"}],
        "claims": [{"ref": "a", "feature": "f", "attribute": "route", "value_geom": line(HOBBS, JAL),
                    **valid(by(SHEET_1937)), "citations": [{"citation": "c37", "role": "map_shows"}]},
                   {"ref": "b", "feature": "f", "attribute": "route", "value_geom": line(HOBBS, MID),
                    **valid(by(SHEET_1955)), "citations": [{"citation": "c55", "role": "map_shows"}]}]})
    section, claims = str(r["ids"]["f"]), {str(r["ids"]["a"]): 1937, str(r["ids"]["b"]): 1955}
    current = max(claims, key=uuid.UUID)
    drawn = sql.execute("SELECT claim_id FROM store.section_route WHERE section_id = %s", (section,)).fetchone()
    assert str(drawn["claim_id"]) == current
    p = props(api["ann"], section)
    assert p["shown_years"] == [claims[current]]
    assert p["accepted"]["route"][0]["claim_id"] == current      # and it leads the list


def test_features_index_and_batches(api):
    section = traced(api, ((-70.0, 44.0), (-70.1, 44.2)), network="water")
    rm, link = removal(api["ann"], line((-70.0, 44.0), (-70.1, 44.2)), section)
    api["bob"].post(f"/links/{link}/reviews", {"verdict": "confirm"})

    # a section and the removal in its scope, asked together and twice over
    got = api["ann"].get("/features", ids=f"{section},{rm},{section}")
    assert [f["id"] for f in got] == [section, rm]
    assert {c["inherited_from"] for c in got[0]["inherited_claims"]} == {rm}
    assert got[1]["inherited_claims"] == [] and [x["id"] for x in got[1]["links"]] == [link]
    assert [x["id"] for x in got[0]["links"]] == [link]

    # a feature that is not a section has no dates
    r = api["ann"].post("/bundles", {"features": [{"ref": "n", "kind": "line", "network": "water"}]})
    [named] = api["ann"].get("/features", ids=str(r["ids"]["n"]))
    assert named["dates"] is None and named["claims"] == []

    # the index: the features of a network or a kind, in id order
    water = api["ann"].get("/features", network="water", limit=200)
    assert {section, str(r["ids"]["n"])} <= {f["id"] for f in water}
    assert [f["id"] for f in water] == sorted(f["id"] for f in water)
    assert {f["kind"] for f in api["ann"].get("/features", network="water", kind="line")} == {"line"}
    api["ann"].get("/features", expect=422, limit=201)
    api["ann"].get("/features", expect=422, kind="pipe")


@pytest.mark.skipif("STUDIO_FIXTURE_OUT" not in os.environ, reason="set STUDIO_FIXTURE_OUT to record")
def test_record_answers_for_studio(api):
    """Records invented answers for Studio's list-versus-detail test
    (studio frontend/src/test/store-recorded.json). Runs only when asked:
    STUDIO_FIXTURE_OUT=<path> uv run pytest tests/test_reads.py -k record"""
    out = os.environ["STUDIO_FIXTURE_OUT"]
    net = "oil"   # a network no other test writes to
    ann, cite = api["ann"], api["cite"]
    s37 = sheet(ann, 1937, SHEET_1937)
    # A: a diameter accepted, a larger one a tool found, and an owner on the section.
    a, _ = trace_from(ann, s37, line((-80.0, 35.0), (-80.1, 35.2)), as_of=SHEET_1937, network=net)
    inches(ann, a, 10, by("1937"))
    inches(cite, a, 12, by("1950"))
    # B: ended, a person matched the line traced as gone to it.
    b, _ = trace_from(ann, s37, line((-81.0, 35.0), (-81.1, 35.2)), as_of=SHEET_1937, network=net)
    _, link = removal(ann, line((-81.0, 35.0), (-81.1, 35.2)), b)
    api["bob"].post(f"/links/{link}/reviews", {"verdict": "confirm"})
    # C: a cut proposed along part of it.
    c, _ = trace_from(ann, s37, line((-82.0, 35.0), (-82.1, 35.4)), as_of=SHEET_1937, network=net)
    removal(ann, line((-82.0, 35.2), (-82.1, 35.4)), c, relation="overlaps", score=0.8)
    # D: a second edition shows it first.
    trace_from(ann, sheet(ann, 1955, "1955-06-30"), line((-83.0, 35.0), (-83.1, 35.2)),
          as_of="1955-06-30", network=net)
    rows = ann.get("/sections", network=net, limit=10000, include="summary")
    links = [x for rel in ("same_as", "overlaps", "part_of")
             for x in ann.get("/links", network=net, relation=rel, limit=10000)]
    ids = sorted({f["properties"]["section_id"] for f in rows["features"]})
    features = ann.get("/features", ids=",".join(ids))
    others = sorted({x["from_feature_id"] for x in links} - set(ids))
    removals = ann.get("/features", ids=",".join(others)) if others else []
    with open(out, "w") as f:
        json.dump({"rows": rows, "links": links, "features": features, "removals": removals},
                  f, indent=1, sort_keys=True)
