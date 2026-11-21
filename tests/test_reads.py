"""The light reads: what `/sections` says about each section beside its line, several features
at once, and links of one network. All data here is made up."""

from __future__ import annotations

import uuid

from lineage_model import FeatureDetail, SectionRow, began, began_between, by
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
    trace,
    valid,
)

WEST = (-104.0, 33.0), (-104.1, 33.4)


def inches(api, section, n, when):
    """A diameter a journal states, in inches."""
    r = api.post("/bundles", {
        "sources": [{"ref": "j", "kind": "issue", "title": "Oil & Gas Journal (made up)"}],
        "citations": [{"ref": "c", "source": "j", "locator": "p. 40"}],
        "claims": [{"ref": "k", "feature": section, "attribute": "diameter", "value_num": n, "value_unit": "in",
                    **valid(when), "citations": [{"citation": "c", "role": "states"}]}]})
    return str(r["ids"]["k"])


def row(api, section, *, row_id=None, network="gas"):
    """The `/sections` row of a section (or of one of its pieces), checked against the shape."""
    features = api.get("/sections", network=network, limit=10000)["features"]
    mine = [f for f in features if f["properties"]["section_id"] == section
            and (row_id is None or f["properties"]["row_id"] == row_id)]
    for f in mine:
        SectionRow.model_validate(f["properties"])
    return mine


def props(api, section, **kw):
    [f] = [f for f in row(api, section, **kw) if f["properties"]["row_id"] == section]
    return f["properties"]


def test_accepted_newest_first_candidates_counted_rejected_left_out(api):
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line(HOBBS, JAL), as_of=SHEET_1937)
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
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line(HOBBS, JAL), as_of=SHEET_1937)
    k = inches(api["ann"], section, 8, by("1937"))
    sql.execute("ALTER TABLE store.claim_citation DISABLE TRIGGER USER")
    try:
        sql.execute("DELETE FROM store.claim_citation WHERE claim_id = %s", (k,))
    finally:
        sql.execute("ALTER TABLE store.claim_citation ENABLE TRIGGER USER")
    assert "diameter" not in props(api["ann"], section)["accepted"]


def test_sheet_dated_by_month_and_no_sheet(api):
    s29 = sheet(api["ann"], 1929, "1929-08")
    section, _ = trace(api["ann"], s29, line(*WEST), as_of="1929-08")
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
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line(HOBBS, JAL), as_of=SHEET_1937)
    rm, link = removal(api["ann"], line(HOBBS, JAL), section)
    assert props(api["ann"], section)["gone_year"] is None          # only proposed so far
    api["bob"].post(f"/links/{link}/reviews", {"verdict": "confirm"})

    p = props(api["ann"], section)
    assert p["gone_year"] == 1960
    statuses = {c["value_text"]: c["inherited_from"] for c in p["accepted"]["status"]}
    assert statuses == {"operational": None, "decommissioned": rm}

    [f] = api["ann"].get("/features", ids=section)
    FeatureDetail.model_validate(f)
    assert not [c for c in f["claims"] if c["value_text"] == "decommissioned"]     # not copied
    inherited = [c for c in f["inherited_claims"] if c["attribute_key"] == "status"]
    assert [(c["value_text"], c["inherited_from"]) for c in inherited] == [("decommissioned", rm)]
    assert inherited[0]["citations"][0]["role"] == "map_omits"
    assert f["dates"]["time_end"] == "1960-12-31"


def test_cut_piece_rows(api):
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line(HOBBS, JAL), as_of=SHEET_1937)
    journal_claim(api["ann"], section, "status", "operational", began("1931-05"))
    _, link = removal(api["ann"], line(MID, JAL), section, relation="overlaps", score=0.98)
    rows = {f["properties"]["row_id"]: f for f in row(api["ann"], section)}
    assert set(rows) == {section, link}
    whole, piece = rows[section]["properties"], rows[link]["properties"]
    # every row of the section carries its route as traced, which no row's geometry is
    assert whole["whole_route"]["coordinates"] == [list(HOBBS), list(JAL)]
    assert piece["whole_route"] == whole["whole_route"]
    assert props(api["ann"], journal_section(api))["whole_route"] is None
    assert whole["accepted"] == piece["accepted"] and piece["section_id"] == section


def journal_section(api):
    """A section with no proposed cut."""
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line(*WEST), as_of=SHEET_1937)
    return section


def test_features_by_ids(api):
    a, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line(HOBBS, JAL), as_of=SHEET_1937)
    b, _ = trace(api["ann"], sheet(api["ann"], 1955, "1955-06-30"), line(*WEST), as_of="1955-06-30")
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
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line((-90.0, 40.0), (-90.1, 40.2)),
                       as_of=SHEET_1937, network="water")
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
    section, _ = trace(api["ann"], s37, line(*WEST), as_of=SHEET_1937)

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
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line(HOBBS, JAL), as_of=SHEET_1937)
    s60 = sheet(api["ann"], 1960, SHEET_1960)
    api["ann"].post("/bundles", {
        "citations": [{"ref": "c", "source": s60}],
        "features": [{"ref": "rm", "kind": "section", "network": "gas"}],
        "claims": [{"feature": "rm", "attribute": "status", "value_text": "decommissioned",
                    **valid(began_between(SHEET_1955, SHEET_1960)),
                    "citations": [{"citation": "c", "role": "map_omits"}]}],
        "links": [{"from": "rm", "to": section, "relation": "overlaps", "score": 0.9}]})
    [r] = row(api["ann"], section)
    assert r["properties"]["whole_route"] is None
    assert len(sql.execute("SELECT 1 FROM qgis.sections WHERE section_id = %s", (section,)).fetchall()) == 1


def test_pieces_of_a_split_inherit_the_parent(api):
    """A confirmed cut splits the section; each piece has the parent's claims through
    `split_into`, in the row and in the feature, marked with where they come from."""
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line(HOBBS, JAL), as_of=SHEET_1937)
    owner = journal_claim(api["ann"], section, "owner", "Made-up Gas Co.", by("1937"))
    _, link = removal(api["ann"], line(MID, JAL), section, relation="overlaps", score=0.98)
    out = api["bob"].post(f"/links/{link}/reviews", {"verdict": "confirm"})
    rest = str(out["continuing_section_id"])

    p = props(api["ann"], rest)
    assert [(c["claim_id"], c["inherited_from"]) for c in p["accepted"]["owner"]] == [(owner, section)]

    [f] = api["ann"].get("/features", ids=rest)
    FeatureDetail.model_validate(f)
    mine = [c for c in f["inherited_claims"] if c["attribute_key"] == "owner"]
    assert [(c["id"], c["inherited_from"]) for c in mine] == [(owner, section)]
    keys = [(c["inherited_from"], c["attribute_key"], c["created_at"]) for c in f["inherited_claims"]]
    assert keys == sorted(keys)                                     # stable: by feature, attribute, time
    assert {x["asserted_by_kind"] for x in f["links"]} == {"person"}


def test_views_are_the_functions_over_every_section(api, sql):
    """`store.claim_scope` and `store.section_dates` are `*_for(NULL)`, and asking for one
    section gives that section's row of the view."""
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line(HOBBS, JAL), as_of=SHEET_1937)
    _, link = removal(api["ann"], line(HOBBS, JAL), section)
    api["bob"].post(f"/links/{link}/reviews", {"verdict": "confirm"})     # dates through scope

    def differ(a, b):
        return sql.execute(f"SELECT count(*) AS n FROM (({a} EXCEPT ALL {b}) UNION ALL ({b} EXCEPT ALL {a})) d"
                           ).fetchone()["n"]

    assert differ("SELECT * FROM store.claim_scope", "SELECT * FROM store.claim_scope_for(NULL)") == 0
    assert differ("SELECT * FROM store.section_dates", "SELECT * FROM store.section_dates_for(NULL)") == 0
    one = sql.execute("SELECT * FROM store.section_dates_for(ARRAY[%s::uuid])", (section,)).fetchall()
    assert one == sql.execute("SELECT * FROM store.section_dates WHERE section_id = %s", (section,)).fetchall()
    assert str(one[0]["time_end"]) == "1960-12-31"


def test_record_answers_for_studio(api, tmp_path):
    """Records invented answers for Studio's list-versus-detail test
    (studio frontend/src/test/store-recorded.json). Runs only when asked:
    STUDIO_FIXTURE_OUT=<path> uv run pytest tests/test_reads.py -k record"""
    import json
    import os

    out = os.environ.get("STUDIO_FIXTURE_OUT")
    if not out:
        import pytest
        pytest.skip("set STUDIO_FIXTURE_OUT to record")
    net = "oil"   # a network no other test writes to
    ann, cite = api["ann"], api["cite"]
    s37 = sheet(ann, 1937, SHEET_1937)
    # A: a diameter accepted, a larger one a tool found, and an owner on the section.
    a, _ = trace(ann, s37, line((-80.0, 35.0), (-80.1, 35.2)), as_of=SHEET_1937, network=net)
    inches(ann, a, 10, by("1937"))
    inches(cite, a, 12, by("1950"))
    # B: ended, a person matched the line traced as gone to it.
    b, _ = trace(ann, s37, line((-81.0, 35.0), (-81.1, 35.2)), as_of=SHEET_1937, network=net)
    _, link = removal(ann, line((-81.0, 35.0), (-81.1, 35.2)), b)
    api["bob"].post(f"/links/{link}/reviews", {"verdict": "confirm"})
    # C: a cut proposed along part of it.
    c, _ = trace(ann, s37, line((-82.0, 35.0), (-82.1, 35.4)), as_of=SHEET_1937, network=net)
    removal(ann, line((-82.0, 35.2), (-82.1, 35.4)), c, relation="overlaps", score=0.8)
    # D: a second edition shows it first.
    trace(ann, sheet(ann, 1955, "1955-06-30"), line((-83.0, 35.0), (-83.1, 35.2)),
          as_of="1955-06-30", network=net)
    rows = ann.get("/sections", network=net, limit=10000)
    links = [x for rel in ("same_as", "overlaps", "part_of")
             for x in ann.get("/links", network=net, relation=rel, limit=10000)]
    ids = sorted({f["properties"]["section_id"] for f in rows["features"]})
    features = ann.get("/features", ids=",".join(ids))
    others = sorted({x["from_feature_id"] for x in links} - set(ids))
    removals = ann.get("/features", ids=",".join(others)) if others else []
    with open(out, "w") as f:
        json.dump({"rows": rows, "links": links, "features": features, "removals": removals},
                  f, indent=1, sort_keys=True)
