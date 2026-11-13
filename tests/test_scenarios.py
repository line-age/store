"""docs/scenarios.md, case by case, through the API. The Hobbs line and its sources are
made up."""

from __future__ import annotations

import datetime as dt

from lineage_model import began, began_between, by

HOBBS, JAL = (-103.136, 32.703), (-103.193, 32.113)
MID = (-103.1645, 32.408)          # halfway
SHEET_1937, SHEET_1955, SHEET_1960 = "1937", "1955-06-30", "1960"


def line(*pts):
    return {"type": "LineString", "coordinates": [list(p) for p in pts]}


def valid(v):
    return {k: (str(x) if isinstance(x, dt.date) else x) for k, x in v.model_dump().items()}


def sheet(api, year, as_of):
    key = {"system": "test", "key": f"sheet:{year}"}
    r = api.post("/bundles", {"sources": [{"ref": "s", "kind": "map_sheet", "title": f"Gas pipelines {year}",
                                           "as_of_edtf": as_of, "external_key": key}]})
    return str(r["ids"]["s"])


def trace(api, sheet_id, geom, network="gas", **extra):
    """A traced line: a section, its route and 'operational by <sheet date>'."""
    as_of = extra.pop("as_of")
    r = api.post("/bundles", {
        "citations": [{"ref": "c", "source": sheet_id, "locator": "the line near Hobbs"}],
        "features": [{"ref": "f", "kind": "section", "network": network}],
        "claims": [
            {"feature": "f", "attribute": "route", "value_geom": geom, **valid(by(as_of)),
             "citations": [{"citation": "c", "role": "map_shows"}], **extra},
            {"feature": "f", "attribute": "status", "value_text": "operational", **valid(by(as_of)),
             "citations": [{"citation": "c", "role": "map_shows"}], **extra},
        ]})
    return str(r["ids"]["f"]), r


def removal(api, geom, to_section, relation="same_as", score=0.97):
    s55, s60 = sheet(api, 1955, SHEET_1955), sheet(api, 1960, SHEET_1960)
    r = api.post("/bundles", {
        "citations": [{"ref": "c55", "source": s55}, {"ref": "c60", "source": s60}],
        "features": [{"ref": "rm", "kind": "section", "network": "gas"}],
        "claims": [
            {"feature": "rm", "attribute": "route", "value_geom": geom, **valid(by(SHEET_1955)),
             "citations": [{"citation": "c55", "role": "map_shows"}]},
            {"feature": "rm", "attribute": "status", "value_text": "decommissioned",
             **valid(began_between(SHEET_1955, SHEET_1960)),
             "citations": [{"citation": "c60", "role": "map_omits"}]},
        ],
        "links": [{"ref": "l", "from": "rm", "to": to_section, "relation": relation, "score": score}]})
    return str(r["ids"]["rm"]), str(r["ids"]["l"])


def journal_claim(api, section, attribute, value, when, quote="", **kw):
    r = api.post("/bundles", {
        "sources": [{"ref": "j", "kind": "issue", "title": "Oil & Gas Journal (made up)"}],
        "citations": [{"ref": "c", "source": "j", "locator": "p. 40", "quote": quote}],
        "claims": [{"ref": "k", "feature": section, "attribute": attribute, "value_text": value,
                    **valid(when), "citations": [{"citation": "c", "role": "states"}], **kw}]})
    return str(r["ids"]["k"])


def rows(sql, section):
    return sql.execute("SELECT * FROM qgis.sections WHERE section_id = %s ORDER BY end_status NULLS FIRST",
                       (section,)).fetchall()


def test_case1_trace_cite_review(api, sql):
    s37 = sheet(api["ann"], 1937, SHEET_1937)
    section, _ = trace(api["ann"], s37, line(HOBBS, JAL), as_of=SHEET_1937)
    claims = api["ann"].get(f"/features/{section}")["claims"]
    assert {c["status"] for c in claims} == {"accepted"}          # a person's claims
    [r] = rows(sql, section)
    assert (r["existed_from"], r["time_start"], r["time_end"]) == ("../1937", dt.date(1937, 1, 1), None)

    # Ask AI: a request, which cite takes and answers with a candidate
    req = api["ann"].post("/citation-requests", {"feature_id": section, "priority": "interactive"})
    work = api["cite"].post("/citation-requests/next")
    assert work["request"]["id"] == req["id"]
    assert any(k["attribute_key"] == "route" for k in work["accepted"])
    claim = journal_claim(api["cite"], section, "status", "operational", began("1931-05"),
                          quote="completed in May 1931", request_id=req["id"])
    api["cite"].post(f"/citation-requests/{req['id']}/complete", {"status": "done", "run": {"model": "test"}})
    assert rows(sql, section)[0]["time_start"] == dt.date(1937, 1, 1)   # candidates move nothing

    api["ann"].post(f"/claims/{claim}/reviews", {"verdict": "accept"})
    [r] = rows(sql, section)
    assert (r["existed_from"], r["time_start"]) == ("1931-05/..", dt.date(1931, 5, 1))

    # reading a fact off the map: a person's claim, accepted at once
    s = api["ann"].post("/bundles", {
        "citations": [{"ref": "c", "source": s37, "locator": "owner label near Hobbs"}],
        "claims": [{"feature": section, "attribute": "owner", "value_text": "New Mexico Gas & Oil Co.",
                    **valid(by(SHEET_1937)), "citations": [{"citation": "c", "role": "map_shows"}]}]})
    assert s["created"]


def test_case2_shutdown_confirmed_and_pinned(api, sql):
    s37 = sheet(api["ann"], 1937, SHEET_1937)
    section, _ = trace(api["ann"], s37, line(HOBBS, JAL), as_of=SHEET_1937)
    rm, link = removal(api["ann"], line(HOBBS, JAL), section)

    [r] = rows(sql, section)                      # the removal isn't drawn; its end is, proposed
    assert (r["end_status"], r["time_end"], r["existed_to"]) == ("proposed", dt.date(1960, 12, 31), "1955-06-30/1960")
    shown = sql.execute("SELECT match_status, removed_between, time_start, time_end FROM qgis.removals "
                        "WHERE removal_id = %s", (rm,)).fetchone()
    # on the slider from the earliest it could have gone, and it never stops having happened
    assert shown == {"match_status": "proposed", "removed_between": "1955-06-30/1960",
                     "time_start": dt.date(1955, 6, 30), "time_end": None}

    api["bob"].post(f"/links/{link}/reviews", {"verdict": "confirm"})
    # QGIS lists what's behind the line: its own claims, and now the removal's, marked inherited
    behind = sql.execute("SELECT attribute, value, role, inherited FROM qgis.claims WHERE section_id = %s",
                         (section,)).fetchall()
    assert {(b["attribute"], b["role"], b["inherited"]) for b in behind if b["attribute"] == "status"} == {
        ("status", "map_shows", False), ("status", "map_omits", True)}

    # resolved: the end date is the section's now, so the removal leaves the QGIS view
    assert sql.execute("SELECT 1 FROM qgis.removals WHERE removal_id = %s", (rm,)).fetchone() is None
    [r] = rows(sql, section)
    assert (r["end_status"], r["time_end"]) == ("confirmed", dt.date(1960, 12, 31))
    # nothing was copied onto the section
    assert not [c for c in api["ann"].get(f"/features/{section}")["claims"] if c["value_text"] == "decommissioned"]

    # a source pins down the year: the narrowest date every claim allows
    k = journal_claim(api["cite"], section, "status", "decommissioned", began("1958-10"))
    api["ann"].post(f"/claims/{k}/reviews", {"verdict": "accept"})
    [r] = rows(sql, section)
    assert (r["time_end"], r["existed_to"], r["date_conflict"]) == (dt.date(1958, 10, 31), "1958-10/..", False)


def test_case2_rejected_link_leaves_line_open(api, sql):
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line(HOBBS, JAL), as_of=SHEET_1937)
    rm, link = removal(api["ann"], line(HOBBS, JAL), section)
    api["ann"].post(f"/links/{link}/reviews", {"verdict": "reject"})
    [r] = rows(sql, section)
    assert (r["time_end"], r["end_status"]) == (None, None)
    assert sql.execute("SELECT match_status FROM qgis.removals WHERE removal_id = %s", (rm,)).fetchone()[
        "match_status"] == "unmatched"


def test_case2_removal_that_is_simply_wrong(api, sql):
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line(HOBBS, JAL), as_of=SHEET_1937)
    rm, _ = removal(api["ann"], line(HOBBS, JAL), section)
    gone = next(c for c in api["ann"].get(f"/features/{rm}")["claims"] if c["value_text"] == "decommissioned")
    api["ann"].post(f"/claims/{gone['id']}/reviews", {"verdict": "reject"})
    # not waiting for review, not drawn as a section, and no proposed end left on the line
    assert sql.execute("SELECT 1 FROM qgis.removals WHERE removal_id = %s", (rm,)).fetchone() is None
    assert rows(sql, rm) == []
    [r] = rows(sql, section)
    assert (r["time_end"], r["end_status"]) == (None, None)


def test_case3_disagreement_widens_and_flags(api, sql):
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line(HOBBS, JAL), as_of=SHEET_1937)
    _, link = removal(api["ann"], line(HOBBS, JAL), section)
    api["ann"].post(f"/links/{link}/reviews", {"verdict": "confirm"})
    k = journal_claim(api["cite"], section, "status", "decommissioned", began("1962"))
    api["ann"].post(f"/claims/{k}/reviews", {"verdict": "accept"})
    [r] = rows(sql, section)
    assert (r["date_conflict"], r["time_end"], r["existed_to"]) == (True, dt.date(1962, 12, 31), "1955-06-30/1962-12-31")


def test_case4_partial_removal_then_split(api, sql):
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line(HOBBS, JAL), as_of=SHEET_1937)
    journal_claim(api["ann"], section, "status", "operational", began("1931-05"))
    _, link = removal(api["ann"], line(MID, JAL), section, relation="overlaps", score=0.98)

    proposed = rows(sql, section)                 # drawn as two pieces, one ending (proposed)
    assert [(r["end_status"], r["time_end"]) for r in proposed] == [(None, None), ("proposed", dt.date(1960, 12, 31))]

    out = api["bob"].post(f"/links/{link}/reviews", {"verdict": "confirm"})
    assert rows(sql, section) == []               # replaced by its pieces
    [ended], [rest] = rows(sql, out["ended_section_id"]), rows(sql, out["continuing_section_id"])
    assert (ended["time_start"], ended["time_end"]) == (dt.date(1931, 5, 1), dt.date(1960, 12, 31))
    assert (rest["time_start"], rest["time_end"]) == (dt.date(1931, 5, 1), None)   # inherited through split_into
    links = api["ann"].get(f"/features/{section}")["links"]
    assert sorted(x["relation"] for x in links if x["status"] == "confirmed") == ["overlaps", "split_into", "split_into"]


def test_case5_traced_twice_keep_other_route(api, sql):
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line(HOBBS, JAL), as_of=SHEET_1937)
    s50 = sheet(api["ann"], 1950, "1950-09-21")
    dup, _ = trace(api["bob"], s50, line(HOBBS, MID, JAL), as_of="1950-09-21")
    link = api["trace"].post("/bundles", {"links": [{"ref": "l", "from": dup, "to": section, "relation": "same_as",
                                                      "score": 0.91}]})["ids"]["l"]
    assert len(rows(sql, section)) == 1 and len(rows(sql, dup)) == 1     # a visible duplicate until decided

    out = api["ann"].post(f"/links/{link}/reviews", {"verdict": "confirm", "keep": "from"})
    assert rows(sql, section) == []              # set aside; the 1950 trace is kept
    [r] = rows(sql, dup)
    assert r["time_start"] == dt.date(1937, 1, 1)  # "by 1937" and "by 1950" give "by 1937"
    assert out["confirmed_link_id"]


def test_case6_rejected_machine_trace(api, sql):
    s37 = sheet(api["ann"], 1937, SHEET_1937)
    rail, _r = trace(api["trace"], s37, line((-103.0, 32.5), (-103.1, 32.6)), as_of=SHEET_1937)
    claims = api["ann"].get(f"/features/{rail}")["claims"]
    assert {c["status"] for c in claims} == {"candidate"}
    assert rows(sql, rail) == []                 # no accepted route, not drawn
    for c in claims:
        api["ann"].post(f"/claims/{c['id']}/reviews", {"verdict": "reject", "note": "railroad"})
    assert sql.execute("SELECT 1 FROM qgis.rejected_traces WHERE section_id = %s", (rail,)).fetchone()


def test_case7_route_corrected(api, sql):
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line(HOBBS, JAL), as_of=SHEET_1937)
    route = next(c for c in api["ann"].get(f"/features/{section}")["claims"] if c["attribute_key"] == "route")
    out = api["bob"].post(f"/claims/{route['id']}/reviews", {
        "verdict": "correct", "replacement": {"value_geom": line(HOBBS, (-103.2, 32.4), JAL), **valid(by(SHEET_1937))}})
    claims = api["ann"].get(f"/features/{section}")["claims"]
    routes = {c["id"]: c for c in claims if c["attribute_key"] == "route"}
    assert routes[route["id"]]["status"] == "rejected"
    new = routes[str(out["replaced_by_claim_id"])]
    assert new["status"] == "accepted" and new["citations"][0]["id"] == route["citations"][0]["id"]
    assert len(rows(sql, section)) == 1


def test_case9_line_membership(api, sql):
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line(HOBBS, JAL), as_of=SHEET_1937)
    r = api["ann"].post("/bundles", {
        "sources": [{"ref": "j", "kind": "issue", "title": "made-up journal"}],
        "citations": [{"ref": "c", "source": "j", "locator": "p. 12"}],
        "features": [{"ref": "line", "kind": "line", "network": "gas", "label": "Hobbs line"}],
        "claims": [{"feature": "line", "attribute": "name", "value_text": "Hobbs line",
                    "citations": [{"citation": "c", "role": "states"}]}],
        "links": [{"from": section, "to": "line", "relation": "part_of", "status": "confirmed"}]})
    assert str(rows(sql, section)[0]["line_id"]) == str(r["ids"]["line"])
    # a part_of link to a section is refused by the database
    api["ann"].post("/bundles", {"links": [{"from": section, "to": section, "relation": "part_of"}]}, expect=422)


def test_case10_parallel(api, sql):
    section, _ = trace(api["ann"], sheet(api["ann"], 1937, SHEET_1937), line(HOBBS, JAL), as_of=SHEET_1937)
    twin, _ = trace(api["ann"], sheet(api["ann"], 1955, SHEET_1955), line((-103.13, 32.703), (-103.187, 32.113)),
                    as_of=SHEET_1955)
    link = api["trace"].post("/bundles", {"links": [{"ref": "l", "from": twin, "to": section,
                                                      "relation": "parallel_to", "score": 0.9}]})["ids"]["l"]
    api["ann"].post(f"/links/{link}/reviews", {"verdict": "confirm"})
    assert len(rows(sql, section)) == 1 and len(rows(sql, twin)) == 1   # two pipes
