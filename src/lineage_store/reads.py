"""Reads. Map-shaped reads come from the same `qgis` views QGIS opens, so the API and QGIS
never disagree about what a section looks like."""

from __future__ import annotations

import datetime as dt
import json
import uuid

from fastapi import HTTPException
from psycopg import Connection


def _feature_collection(rows: list[dict]) -> dict:
    feats = []
    for r in rows:
        geom = json.loads(r.pop("geometry")) if r.get("geometry") else None
        feats.append({"type": "Feature", "id": str(r["row_id"]), "geometry": geom,
                      "properties": {k: (str(v) if isinstance(v, (uuid.UUID, dt.date)) else v)
                                     for k, v in r.items()}})
    return {"type": "FeatureCollection", "features": feats}


def sections(conn: Connection, bbox: tuple[float, float, float, float] | None, at: dt.date | None,
             network: str | None, limit: int) -> dict:
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
    about = section_summaries(conn, list({r["section_id"] for r in rows}))
    for r in rows:
        extra = dict(about.get(r["section_id"], _NO_SUMMARY))
        if extra["whole_route"] is None:
            del extra["whole_route"]      # only where a proposed cut makes rows parts of the route
        r.update(extra)
    return _feature_collection(rows)


_NO_SUMMARY = {"accepted": {}, "candidates": {}, "shown_years": [], "gone_year": None, "created_at": None,
               "whole_route": None}


def section_summaries(conn: Connection, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """What a list of sections shows without asking for each one: per attribute, the accepted
    claims with at least one citation (the section's own and those it has through confirmed
    links, store's scope), raw and newest first; how many cited candidates wait on each
    attribute; the years of the map sheets that show its route; the year of the sheet that
    no longer shows it, once a person confirmed which removal ended it.

    A client words the values itself; nothing here is worded, so a list row and a section's
    detail can be worded by one function and never disagree."""
    if not ids:
        return {}
    # Compiling this query costs more than running it (0.3 s of 0.9 s at 10,000 sections).
    conn.execute("SET LOCAL jit = off")
    rows = conn.execute(
        """WITH ids AS (SELECT DISTINCT unnest(%(ids)s::uuid[]) AS id),
           scope AS (SELECT * FROM store.claim_scope_for(%(ids)s::uuid[])),
           facts AS (
               SELECT sc.section_id, c.attribute_key AS key, c.created_at, c.id,
                      json_build_object(
                          'claim_id', c.id, 'value_text', c.value_text, 'value_num', c.value_num,
                          'value_unit', c.value_unit, 'valid_edtf', c.valid_edtf, 'created_at', c.created_at,
                          'shown_as_of', sh.as_of,
                          'inherited_from', nullif(sc.feature_id, sc.section_id)) AS fact
               FROM scope sc
               JOIN store.claim c ON c.feature_id = sc.feature_id AND c.status = 'accepted'
               LEFT JOIN LATERAL (
                   SELECT min(s.as_of_edtf COLLATE "C") AS as_of
                   FROM store.claim_citation cc JOIN store.citation ci ON ci.id = cc.citation_id
                   JOIN store.source s ON s.id = ci.source_id
                   WHERE cc.claim_id = c.id AND cc.role = 'map_shows' AND s.kind = 'map_sheet') sh ON true
               WHERE EXISTS (SELECT 1 FROM store.claim_citation cc WHERE cc.claim_id = c.id)
                 AND (c.attribute_key <> 'route' OR c.value_geom IS NOT NULL)
           ),
           accepted AS (
               SELECT section_id, json_object_agg(key, items) AS accepted
               FROM (SELECT section_id, key, json_agg(fact ORDER BY created_at DESC, id) AS items
                     FROM facts GROUP BY section_id, key) k
               GROUP BY section_id
           ),
           candidates AS (
               SELECT section_id, json_object_agg(key, n) AS candidates
               FROM (SELECT c.feature_id AS section_id, c.attribute_key AS key, count(*) AS n
                     FROM ids JOIN store.claim c ON c.feature_id = ids.id
                     WHERE c.status = 'candidate'
                       AND (c.value_text <> '' OR c.value_num IS NOT NULL OR c.value_geom IS NOT NULL)
                       AND EXISTS (SELECT 1 FROM store.claim_citation cc WHERE cc.claim_id = c.id)
                     GROUP BY 1, 2) k
               GROUP BY section_id
           ),
           shown AS (
               SELECT r.section_id,
                      array_agg(DISTINCT substring(s.as_of_edtf FROM '[0-9]{4}')::int) AS years
               FROM ids JOIN store.section_route r ON r.section_id = ids.id
               JOIN store.claim_citation cc ON cc.claim_id = r.claim_id AND cc.role = 'map_shows'
               JOIN store.citation ci ON ci.id = cc.citation_id
               JOIN store.source s ON s.id = ci.source_id
               WHERE s.kind = 'map_sheet' AND s.as_of_edtf ~ '[0-9]{4}'
               GROUP BY r.section_id
           ),
           gone AS (   -- a removal a person linked to the section: the sheet that leaves it out
               SELECT sc.section_id, min(substring(s.as_of_edtf FROM '[0-9]{4}')::int) AS year
               FROM scope sc
               JOIN store.removal rm ON rm.feature_id = sc.feature_id AND sc.feature_id <> sc.section_id
               JOIN store.claim c ON c.feature_id = rm.feature_id AND c.attribute_key = 'status'
                   AND c.status = 'accepted'
               JOIN store.claim_citation cc ON cc.claim_id = c.id AND cc.role = 'map_omits'
               JOIN store.citation ci ON ci.id = cc.citation_id
               JOIN store.source s ON s.id = ci.source_id
               WHERE s.kind = 'map_sheet' AND s.as_of_edtf ~ '[0-9]{4}'
               GROUP BY sc.section_id
           ),
           cut AS (    -- a proposed cut: the drawn row is the route without it
               SELECT DISTINCT l.to_feature_id AS section_id
               FROM ids JOIN store.feature_link l ON l.to_feature_id = ids.id
               JOIN store.removal rm ON rm.feature_id = l.from_feature_id AND rm.standing
               WHERE l.relation = 'overlaps' AND l.status = 'proposed'
           )
           SELECT ids.id AS section_id, f.created_at,
                  coalesce(a.accepted, '{}') AS accepted, coalesce(k.candidates, '{}') AS candidates,
                  coalesce(sh.years, '{}') AS shown_years, g.year AS gone_year,
                  CASE WHEN cut.section_id IS NOT NULL THEN ST_AsGeoJSON(r.geom)::json END AS whole_route
           FROM ids JOIN store.feature f ON f.id = ids.id
           LEFT JOIN accepted a ON a.section_id = ids.id
           LEFT JOIN candidates k ON k.section_id = ids.id
           LEFT JOIN shown sh ON sh.section_id = ids.id
           LEFT JOIN gone g ON g.section_id = ids.id
           LEFT JOIN cut ON cut.section_id = ids.id
           LEFT JOIN store.section_route r ON r.section_id = cut.section_id""",
        {"ids": ids}).fetchall()
    out = {}
    for r in rows:
        sid = r.pop("section_id")
        r["shown_years"] = sorted(r["shown_years"])
        r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
        out[sid] = r
    return out


def feature(conn: Connection, feature_id: uuid.UUID) -> dict:
    found = features(conn, [feature_id])
    if not found:
        raise HTTPException(404, "no such feature")
    return found[0]


_CLAIM_COLUMNS = """c.id, c.feature_id, c.attribute_key, c.value_text, c.value_num, c.value_unit,
       ST_AsGeoJSON(c.value_geom)::json AS value_geom, c.value_feature_id, c.valid_edtf,
       c.start_earliest, c.start_latest, c.end_earliest, c.end_latest, c.status,
       c.request_id, c.created_at, a.id AS asserted_by, a.name AS asserted_by_name, a.kind AS asserted_by_kind"""


def features(conn: Connection, ids: list[uuid.UUID]) -> list[dict]:
    """Several features at once, in the order asked, each as `/features/{id}` gives it; an id
    store does not hold is left out. Besides its own `claims`, a section carries
    `inherited_claims`: the claims it has through confirmed links (store's scope: a removal
    a person matched to it, the section it was split from), each with `inherited_from`.
    They stay that feature's claims; nothing is merged."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    found = {f["id"]: f for f in conn.execute("SELECT * FROM store.feature WHERE id = ANY(%s)", (ids,)).fetchall()}
    ids = [i for i in ids if i in found]
    if not ids:
        return []
    scope = conn.execute("SELECT section_id, feature_id FROM store.claim_scope_for(%s::uuid[]) "
                         "WHERE section_id <> feature_id", (ids,)).fetchall()
    owners = list({*ids, *(s["feature_id"] for s in scope)})
    claims = conn.execute(
        f"""SELECT {_CLAIM_COLUMNS} FROM store.claim c JOIN store.agent a ON a.id = c.asserted_by
            WHERE c.feature_id = ANY(%s) ORDER BY c.feature_id, c.attribute_key, c.created_at""",
        (owners,)).fetchall()
    claim_ids = [c["id"] for c in claims]
    cites = conn.execute(
        """SELECT cc.claim_id, cc.role, cc.confidence, ci.id, ci.locator, ci.quote, ci.url, ci.archive_url,
                  s.id AS source_id, s.kind AS source_kind, s.title AS source_title,
                  s.published_edtf, s.as_of_edtf, s.identifier
           FROM store.claim_citation cc JOIN store.citation ci ON ci.id = cc.citation_id
           JOIN store.source s ON s.id = ci.source_id WHERE cc.claim_id = ANY(%s)""", (claim_ids,)).fetchall()
    reviews = conn.execute(
        """SELECT r.*, a.name AS reviewer_name FROM store.review r JOIN store.agent a ON a.id = r.reviewer_id
           WHERE r.claim_id = ANY(%s) ORDER BY r.created_at""", (claim_ids,)).fetchall()
    cites_of, reviews_of, claims_of = {}, {}, {}
    for x in cites:
        cites_of.setdefault(x["claim_id"], []).append(x)
    for x in reviews:
        reviews_of.setdefault(x["claim_id"], []).append(x)
    for c in claims:
        c["citations"] = cites_of.get(c["id"], [])
        c["reviews"] = reviews_of.get(c["id"], [])
        claims_of.setdefault(c.pop("feature_id"), []).append(c)
    links = conn.execute(
        """SELECT l.*, a.name AS asserted_by_name FROM store.feature_link l JOIN store.agent a ON a.id = l.asserted_by
           WHERE l.from_feature_id = ANY(%s) OR l.to_feature_id = ANY(%s) ORDER BY l.created_at""",
        (ids, ids)).fetchall()
    keys = conn.execute("SELECT entity_id, system, key FROM store.external_key WHERE entity_id = ANY(%s)",
                        (ids,)).fetchall()
    dates = {d.pop("section_id"): d for d in conn.execute(
        "SELECT section_id, existed_from, existed_to, time_start, time_end, date_conflict "
        "FROM store.section_dates_for(%s::uuid[])", (ids,)).fetchall()}
    out = []
    for i in ids:
        inherited = [{**c, "inherited_from": s["feature_id"]}
                     for s in scope if s["section_id"] == i for c in claims_of.get(s["feature_id"], [])]
        out.append({**found[i], "dates": dates.get(i), "claims": claims_of.get(i, []),
                    "inherited_claims": inherited,
                    "links": [x for x in links if i in (x["from_feature_id"], x["to_feature_id"])],
                    "external_keys": [{"system": k["system"], "key": k["key"]} for k in keys if k["entity_id"] == i]})
    return out


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


def links(conn: Connection, status: str | None, relation: str | None, limit: int,
          network: str | None = None) -> list[dict]:
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
