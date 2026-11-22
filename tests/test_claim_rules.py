"""What a claim's value may be, through the API, whoever writes it: the attribute applies to
the feature's kind and network, the value is of the attribute's type, an enum value is one of
its words. A refusal is a 422 with the rule in words, and a refused bundle writes nothing.
All data is made up (L-0009)."""

from __future__ import annotations

from test_scenarios import HOBBS, JAL, SHEET_1937, line, sheet, trace


def _setup(api):
    s = sheet(api, 1937, SHEET_1937)
    section, _ = trace(api, s, line(HOBBS, JAL), as_of=SHEET_1937)
    return s, section


def _claim(source, feature, **value):
    return {"citations": [{"ref": "c", "source": source}],
            "claims": [{"ref": "k", "feature": feature, **value, "citations": [{"citation": "c", "role": "states"}]}]}


def refused(api, body):
    return api.post("/bundles", body, expect=422)["detail"]


def test_an_attribute_applies_only_to_its_feature_kinds(api):
    s, _ = _setup(api["ann"])
    line_id = str(api["ann"].post("/bundles", {"features": [{"ref": "l", "kind": "line", "network": "gas"}]})["ids"]["l"])
    assert refused(api["ann"], _claim(s, line_id, attribute="diameter", value_num=12, value_unit="in")) \
        == "diameter does not apply to a line"


def test_an_attribute_applies_only_to_its_networks(api):
    s, section = _setup(api["ann"])
    assert refused(api["ann"], _claim(s, section, attribute="voltage", value_num=69, value_unit="kV")) \
        == "voltage does not apply to the gas network"


def test_a_value_is_of_its_attributes_type(api):
    s, section = _setup(api["ann"])
    assert refused(api["ann"], _claim(s, section, attribute="owner")) == "owner needs a text value"
    assert refused(api["ann"], _claim(s, section, attribute="status")) == "status needs a enum value"
    assert refused(api["ann"], _claim(s, section, attribute="diameter", value_text="twelve")) \
        == "diameter needs a quantity value"
    assert refused(api["ann"], _claim(s, section, attribute="route")) == "route needs a geometry value"


def test_a_feature_value_needs_a_feature(api):
    s, _ = _setup(api["ann"])
    station = str(api["ann"].post("/bundles", {"features": [{"ref": "st", "kind": "station", "network": "gas"}]})["ids"]["st"])
    assert refused(api["ann"], _claim(s, station, attribute="serves")) == "serves needs a feature value"


def test_an_enum_value_is_one_of_its_words(api):
    s, section = _setup(api["ann"])
    assert refused(api["ann"], _claim(s, section, attribute="status", value_text="abandoned-ish")) == \
        "abandoned-ish is not a value of status (proposed, permitted, under_construction, operational, " \
        "maintenance, decommissioned)"
    assert refused(api["ann"], _claim(s, section, attribute="commodity", value_text="natural")) \
        == "natural is not a value of commodity ()"


def test_a_claim_that_fits_is_taken(api):
    s, section = _setup(api["ann"])
    api["ann"].post("/bundles", _claim(s, section, attribute="diameter", value_num=12, value_unit="in"))
    api["ann"].post("/bundles", _claim(s, section, attribute="owner", value_text="Example Gas Co."))


def test_a_refused_bundle_writes_nothing(api, sql):
    s, section = _setup(api["ann"])
    before = sql.execute("SELECT count(*) AS n FROM store.claim WHERE feature_id = %s", (section,)).fetchone()["n"]
    body = {"citations": [{"ref": "c", "source": s}], "claims": [
        {"feature": section, "attribute": "owner", "value_text": "Example Gas Co.",
         "citations": [{"citation": "c", "role": "states"}]},
        {"feature": section, "attribute": "status", "value_text": "abandoned-ish",
         "citations": [{"citation": "c", "role": "states"}]}]}
    refused(api["ann"], body)
    after = sql.execute("SELECT count(*) AS n FROM store.claim WHERE feature_id = %s", (section,)).fetchone()["n"]
    assert after == before


def test_a_correction_is_held_to_the_same_rules(api):
    s, section = _setup(api["ann"])
    claim_id = str(api["ann"].post("/bundles", _claim(s, section, attribute="owner", value_text="Example Gas Co."))["ids"]["k"])
    bad = {"verdict": "correct", "replacement": {"value_text": None}}
    assert api["ann"].post(f"/claims/{claim_id}/reviews", bad, expect=422)["detail"] == "owner needs a text value"
