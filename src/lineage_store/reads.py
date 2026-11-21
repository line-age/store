"""Reads. Map-shaped reads come from the same `qgis` views QGIS opens, so the API and QGIS
never disagree about what a section looks like."""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from collections import defaultdict

from psycopg import Connection


def _feature_collection(rows: list[dict]) -> dict:
    feats = []
    for r in rows:
        geom = json.loads(r.pop("geometry")) if r.get("geometry") else None
        feats.append({"type": "Feature", "id": str(r["row_id"]), "geometry": geom, "properties": r})
    return {"type": "FeatureCollection", "features": feats}


def sections(conn: Connection, bbox: tuple[float, float, float, float] | None, at: dt.date | None,
             network: str | None, limit: int, summary: bool = False) -> dict:
    """The drawn rows of `qgis.sections`. With `summary`, each row also carries what
    `section_summaries` says of its section; a section drawn as several rows (a proposed
    cut) says it on each."""
    where, args = [], []
    if bbox:
        where.append("geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)")
        args += list(bbox)
    if at:
        where.append("(time_start IS NULL OR time_start <= %s) AND (time_end IS NULL OR time_end >= %s)")
        args += [at, at]
    if network:
        where.append("network = %s")
        args.append(network)
    sql = ("SELECT ST_AsGeoJSON(geom) AS geometry, row_id, section_id, label, network, existed_from, existed_to, "
           "time_start, time_end, end_status, date_conflict, line_id, diameter, has_candidates FROM qgis.sections"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY row_id LIMIT %s")
    rows = conn.execute(sql, [*args, limit]).fetchall()
    if summary:
        # every drawn section is a feature, so each row has its summary
        about = section_summaries(conn, list({r["section_id"] for r in rows}))
        for r in rows:
            r.update(about[r["section_id"]])
    return _feature_collection(rows)


_YEAR = re.compile(r"[0-9]{4}")


def _year(as_of: str | None) -> int | None:
    found = _YEAR.search(as_of or "")
    return int(found.group()) if found else None


def section_summaries(conn: Connection, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """What a list of sections shows without asking for each one. The database is asked for
    the rows (scope, claims, their citations, proposed cuts); `summarise` holds the rules."""
    if not ids:
        return {}
    created = {r["id"]: r["created_at"] for r in conn.execute(
        "SELECT id, created_at FROM store.feature WHERE id = ANY(%s)", (ids,))}
    scope = defaultdict(list)
    for r in conn.execute("SELECT section_id, feature_id FROM store.claim_scope_for(%s::uuid[])", (ids,)):
        scope[r["section_id"]].append(r["feature_id"])
    owners = list({*ids, *(f for fs in scope.values() for f in fs)})
    claims = defaultdict(list)
    for c in conn.execute(
            """SELECT c.id, c.feature_id, c.attribute_key, c.status, c.value_text, c.value_num, c.value_unit,
                      c.valid_edtf, c.created_at, c.value_geom IS NOT NULL AS has_geom,
                      a.id AS asserted_by, a.name AS asserted_by_name, a.kind AS asserted_by_kind
               FROM store.claim c JOIN store.agent a ON a.id = c.asserted_by
               WHERE c.feature_id = ANY(%s) AND c.status IN ('accepted', 'candidate')""",
            (owners,)):
        claims[c["feature_id"]].append(c)
    cites = defaultdict(list)
    for x in conn.execute(
            """SELECT cc.claim_id, cc.role, s.kind, s.as_of_edtf
               FROM store.claim_citation cc JOIN store.citation ci ON ci.id = cc.citation_id
               JOIN store.source s ON s.id = ci.source_id WHERE cc.claim_id = ANY(%s)""",
            ([c["id"] for cs in claims.values() for c in cs],)):
        cites[x["claim_id"]].append(x)
    # A proposed cut, as store.proposed_end has it: a standing removal with a route of its own.
    # Asked of the tables, per id: the views work out every removal and route first.
    whole = {r["section_id"]: r["whole_route"] for r in conn.execute(
        """SELECT DISTINCT ON (l.to_feature_id) l.to_feature_id AS section_id,
                  (SELECT ST_AsGeoJSON(c.value_geom)::json FROM store.claim c
                   WHERE c.feature_id = l.to_feature_id AND c.attribute_key = 'route' AND c.status = 'accepted'
                   ORDER BY c.created_at DESC, c.id DESC LIMIT 1) AS whole_route
           FROM store.feature_link l
           WHERE l.relation = 'overlaps' AND l.status = 'proposed' AND l.to_feature_id = ANY(%s)
             AND EXISTS (SELECT 1 FROM store.claim d
                         WHERE d.feature_id = l.from_feature_id AND d.attribute_key = 'route'
                           AND d.status = 'accepted')
             AND EXISTS (SELECT 1 FROM store.claim d
                         JOIN store.claim_citation dc ON dc.claim_id = d.id AND dc.role = 'map_omits'
                         WHERE d.feature_id = l.from_feature_id AND d.attribute_key = 'status'
                           AND d.value_text = 'decommissioned' AND d.status = 'accepted')""", (ids,))}
    return {i: summarise(i, scope[i], claims, cites, created[i], whole.get(i)) for i in ids if i in created}


def summarise(section: uuid.UUID, scope: list[uuid.UUID], claims: dict[uuid.UUID, list[dict]],
              cites: dict[uuid.UUID, list[dict]], created_at: dt.datetime, whole_route: dict | None) -> dict:
    """One section's summary, from the claims of the features in its scope and their citations.

    - `accepted`: per attribute, the accepted claims with at least one citation, the
      section's own and those of its scope (marked `inherited_from`), raw and newest first.
      A client words the values, so a list row and a section's detail never disagree.
    - `candidates`: per attribute, how many of its own cited candidates with a value wait.
    - `shown_years`: the years of the map sheets cited as showing its current route.
    - `gone_year`: the year of the sheet that leaves it out, once a person confirmed which
      removal ended it (the removal is then in its scope).
    - `whole_route`: its route as traced, when a proposed cut draws it in pieces.
    - `traced_by`: the person who asserted its current route, by id and name; null when
      it has no accepted route or a tool asserted it. A list can then be narrowed to one
      tracer's sections without asking for each section.

    A sheet's year is the first four digits of its date as given; a date without four
    digits (`193X`) gives no year."""
    def sheets(claim, role):
        return [x["as_of_edtf"] for x in cites.get(claim["id"], ())
                if x["role"] == role and x["kind"] == "map_sheet" and x["as_of_edtf"] is not None]

    accepted = defaultdict(list)
    gone = []
    for feature in scope:
        for c in claims.get(feature, ()):
            if c["status"] != "accepted":
                continue
            if feature != section and c["attribute_key"] == "status" and c["value_text"] == "decommissioned":
                gone += [y for y in map(_year, sheets(c, "map_omits")) if y is not None]
            if not cites.get(c["id"]) or (c["attribute_key"] == "route" and not c["has_geom"]):
                continue
            shown = sorted(sheets(c, "map_shows"), key=lambda a: (_year(a) is None, _year(a) or 0, a))
            accepted[c["attribute_key"]].append({
                "claim_id": c["id"], "value_text": c["value_text"], "value_num": c["value_num"],
                "value_unit": c["value_unit"], "valid_edtf": c["valid_edtf"], "created_at": c["created_at"],
                "shown_as_of": shown[0] if shown else None,
                "inherited_from": feature if feature != section else None})
    for facts in accepted.values():
        facts.sort(key=lambda f: f["claim_id"])
        facts.sort(key=lambda f: f["created_at"], reverse=True)
    own = claims.get(section, ())
    candidates = defaultdict(int)
    for c in own:
        if (c["status"] == "candidate" and cites.get(c["id"])
                and (c["value_text"] or c["value_num"] is not None or c["has_geom"])):
            candidates[c["attribute_key"]] += 1
    routes = [c for c in own if c["status"] == "accepted" and c["attribute_key"] == "route"]
    route = max(routes, key=lambda c: (c["created_at"], c["id"]), default=None)
    years = sorted({y for y in map(_year, sheets(route, "map_shows")) if y is not None}) if route else []
    tracer = ({"id": route["asserted_by"], "name": route["asserted_by_name"]}
              if route and route["asserted_by_kind"] == "person" else None)
    return {"created_at": created_at, "accepted": dict(accepted), "candidates": dict(candidates),
            "shown_years": years, "gone_year": min(gone, default=None), "whole_route": whole_route,
            "traced_by": tracer}


def feature_ids(conn: Connection, network: str | None, kind: str | None, limit: int) -> list[uuid.UUID]:
    """The features of a network or a kind, in id order."""
    return [r["id"] for r in conn.execute(
        """SELECT id FROM store.feature
           WHERE (%(network)s::text IS NULL OR network = %(network)s)
             AND (%(kind)s::text IS NULL OR kind = %(kind)s)
           ORDER BY id LIMIT %(limit)s""", {"network": network, "kind": kind, "limit": limit})]


def features(conn: Connection, ids: list[uuid.UUID]) -> list[dict]:
    """Features in the order asked, each with its claims (and their citations and reviews),
    links, external keys and dates; an id store does not hold is left out. Besides its own
    `claims`, a section carries `inherited_claims`: the claims it has through confirmed
    links (store's scope: a removal a person matched to it, the section it was split from),
    each with `inherited_from`. They stay that feature's claims; nothing is merged."""
    found = {f["id"]: f for f in conn.execute("SELECT * FROM store.feature WHERE id = ANY(%s)", (ids,))}
    ids = [i for i in dict.fromkeys(ids) if i in found]
    if not ids:
        return []
    scope: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for s in conn.execute("SELECT section_id, feature_id FROM store.claim_scope_for(%s::uuid[]) "
                          "WHERE section_id <> feature_id ORDER BY section_id, feature_id", (ids,)):
        scope[s["section_id"]].append(s["feature_id"])
    owners = list({*ids, *(f for fs in scope.values() for f in fs)})
    claims = conn.execute(
        """SELECT c.id, c.feature_id, c.attribute_key, c.value_text, c.value_num, c.value_unit,
                  ST_AsGeoJSON(c.value_geom)::json AS value_geom, c.value_feature_id, c.valid_edtf,
                  c.start_earliest, c.start_latest, c.end_earliest, c.end_latest, c.status, c.request_id,
                  c.created_at, a.id AS asserted_by, a.name AS asserted_by_name, a.kind AS asserted_by_kind
           FROM store.claim c JOIN store.agent a ON a.id = c.asserted_by
           WHERE c.feature_id = ANY(%s) ORDER BY c.feature_id, c.attribute_key, c.created_at, c.id""",
        (owners,)).fetchall()
    claim_ids = [c["id"] for c in claims]
    cites_of: dict[uuid.UUID, list[dict]] = defaultdict(list)
    for x in conn.execute(
            """SELECT cc.claim_id, cc.role, cc.confidence, ci.id, ci.locator, ci.quote, ci.url, ci.archive_url,
                      s.id AS source_id, s.kind AS source_kind, s.title AS source_title,
                      s.published_edtf, s.as_of_edtf, s.identifier
               FROM store.claim_citation cc JOIN store.citation ci ON ci.id = cc.citation_id
               JOIN store.source s ON s.id = ci.source_id WHERE cc.claim_id = ANY(%s)""", (claim_ids,)):
        cites_of[x["claim_id"]].append(x)
    reviews_of: dict[uuid.UUID, list[dict]] = defaultdict(list)
    for x in conn.execute(
            """SELECT r.*, a.name AS reviewer_name FROM store.review r JOIN store.agent a ON a.id = r.reviewer_id
               WHERE r.claim_id = ANY(%s) ORDER BY r.created_at""", (claim_ids,)):
        reviews_of[x["claim_id"]].append(x)
    claims_of: dict[uuid.UUID, list[dict]] = defaultdict(list)
    for c in claims:
        c["citations"] = cites_of.get(c["id"], [])
        c["reviews"] = reviews_of.get(c["id"], [])
        claims_of[c.pop("feature_id")].append(c)
    wanted = set(ids)
    links_of: dict[uuid.UUID, list[dict]] = defaultdict(list)
    for x in conn.execute(
            """SELECT l.*, a.name AS asserted_by_name, a.kind AS asserted_by_kind
               FROM store.feature_link l JOIN store.agent a ON a.id = l.asserted_by
               WHERE l.from_feature_id = ANY(%s) OR l.to_feature_id = ANY(%s) ORDER BY l.created_at""",
            (ids, ids)):
        for end in {x["from_feature_id"], x["to_feature_id"]} & wanted:
            links_of[end].append(x)
    keys_of: dict[uuid.UUID, list[dict]] = defaultdict(list)
    for k in conn.execute("SELECT entity_id, system, key FROM store.external_key WHERE entity_id = ANY(%s)",
                          (ids,)):
        keys_of[k["entity_id"]].append({"system": k["system"], "key": k["key"]})
    dates = {d.pop("section_id"): d for d in conn.execute(
        "SELECT section_id, existed_from, existed_to, time_start, time_end, date_conflict "
        "FROM store.section_dates_for(%s::uuid[])", (ids,))}
    return [{**found[i], "dates": dates.get(i), "claims": claims_of.get(i, []),
             # in scope order (by feature), each feature's claims by attribute, oldest first
             "inherited_claims": [{**c, "inherited_from": f} for f in scope.get(i, ())
                                  for c in claims_of.get(f, ())],
             "links": links_of.get(i, []), "external_keys": keys_of.get(i, [])} for i in ids]


def missing(conn: Connection, attribute: str, network: str | None, limit: int) -> list[dict]:
    """Drawn sections with no accepted claim for `attribute`, counting claims they inherit
    through confirmed links. What `review` filters on to see where the loop isn't done."""
    return conn.execute(
        """SELECT d.section_id, f.label, f.network FROM store.drawn_section d
           JOIN store.feature f ON f.id = d.section_id
           WHERE (%s::text IS NULL OR f.network = %s)
             AND NOT EXISTS (SELECT 1 FROM store.claim_scope sc JOIN store.claim c ON c.feature_id = sc.feature_id
                             WHERE sc.section_id = d.section_id AND c.attribute_key = %s AND c.status = 'accepted')
           ORDER BY d.section_id LIMIT %s""", (network, network, attribute, limit)).fetchall()


def links(conn: Connection, status: str | None, relation: str | None, network: str | None,
          limit: int) -> list[dict]:
    """Links, oldest first. With `network`, only those with a feature of that network at
    either end. Each names who asserted it and what kind of agent that is, so a client can
    show a tool's name and never a person's."""
    return conn.execute(
        """SELECT l.*, a.name AS asserted_by_name, a.kind AS asserted_by_kind
           FROM store.feature_link l JOIN store.agent a ON a.id = l.asserted_by
           WHERE (%(status)s::text IS NULL OR l.status = %(status)s)
             AND (%(relation)s::text IS NULL OR l.relation = %(relation)s)
             AND (%(network)s::text IS NULL OR EXISTS (
                     SELECT 1 FROM store.feature f
                     WHERE f.id IN (l.from_feature_id, l.to_feature_id) AND f.network = %(network)s))
           ORDER BY l.created_at LIMIT %(limit)s""",
        {"status": status, "relation": relation, "network": network, "limit": limit}).fetchall()


def requests(conn: Connection, status: str | None, limit: int) -> list[dict]:
    return conn.execute(
        """SELECT * FROM store.citation_request WHERE (%s::text IS NULL OR status = %s)
           ORDER BY priority = 'interactive' DESC, created_at LIMIT %s""", (status, status, limit)).fetchall()


def request_context(conn: Connection, req: dict) -> dict:
    """What cite needs to work a request: the request and everything accepted about the
    section, including what it inherits through confirmed links."""
    known = conn.execute(
        """SELECT c.attribute_key, c.value_text, c.value_num, c.value_unit, c.valid_edtf,
                  ST_AsGeoJSON(c.value_geom)::json AS value_geom
           FROM store.claim_scope sc JOIN store.claim c ON c.feature_id = sc.feature_id
           WHERE sc.section_id = %s AND c.status = 'accepted' ORDER BY c.attribute_key""",
        (req["feature_id"],)).fetchall()
    rejected = conn.execute(
        """SELECT c.attribute_key, c.value_text, c.value_num, c.value_unit, c.valid_edtf
           FROM store.claim c WHERE c.feature_id = %s AND c.status = 'rejected'""", (req["feature_id"],)).fetchall()
    return {"request": req, "accepted": known, "rejected": rejected}
