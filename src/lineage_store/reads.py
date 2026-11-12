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
    return _feature_collection(conn.execute(sql, args + [limit]).fetchall())


def feature(conn: Connection, feature_id: uuid.UUID) -> dict:
    f = conn.execute("SELECT * FROM store.feature WHERE id = %s", (feature_id,)).fetchone()
    if not f:
        raise HTTPException(404, "no such feature")
    claims = conn.execute(
        """SELECT c.id, c.attribute_key, c.value_text, c.value_num, c.value_unit,
                  ST_AsGeoJSON(c.value_geom)::json AS value_geom, c.value_feature_id, c.valid_edtf,
                  c.start_earliest, c.start_latest, c.end_earliest, c.end_latest, c.status,
                  c.request_id, c.created_at, a.id AS asserted_by, a.name AS asserted_by_name, a.kind AS asserted_by_kind
           FROM store.claim c JOIN store.agent a ON a.id = c.asserted_by
           WHERE c.feature_id = %s ORDER BY c.attribute_key, c.created_at""", (feature_id,)).fetchall()
    ids = [c["id"] for c in claims]
    cites = conn.execute(
        """SELECT cc.claim_id, cc.role, cc.confidence, ci.id, ci.locator, ci.quote, ci.url, ci.archive_url,
                  s.id AS source_id, s.kind AS source_kind, s.title AS source_title,
                  s.published_edtf, s.as_of_edtf, s.identifier
           FROM store.claim_citation cc JOIN store.citation ci ON ci.id = cc.citation_id
           JOIN store.source s ON s.id = ci.source_id WHERE cc.claim_id = ANY(%s)""", (ids,)).fetchall()
    reviews = conn.execute(
        """SELECT r.*, a.name AS reviewer_name FROM store.review r JOIN store.agent a ON a.id = r.reviewer_id
           WHERE r.claim_id = ANY(%s) ORDER BY r.created_at""", (ids,)).fetchall()
    for c in claims:
        c["citations"] = [x for x in cites if x["claim_id"] == c["id"]]
        c["reviews"] = [x for x in reviews if x["claim_id"] == c["id"]]
    links = conn.execute(
        """SELECT l.*, a.name AS asserted_by_name FROM store.feature_link l JOIN store.agent a ON a.id = l.asserted_by
           WHERE l.from_feature_id = %s OR l.to_feature_id = %s ORDER BY l.created_at""",
        (feature_id, feature_id)).fetchall()
    keys = conn.execute("SELECT system, key FROM store.external_key WHERE entity_id = %s", (feature_id,)).fetchall()
    dates = conn.execute("SELECT existed_from, existed_to, time_start, time_end, date_conflict "
                         "FROM store.section_dates WHERE section_id = %s", (feature_id,)).fetchone()
    return {**f, "dates": dates, "claims": claims, "links": links, "external_keys": keys}


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


def links(conn: Connection, status: str | None, relation: str | None, limit: int) -> list[dict]:
    return conn.execute(
        """SELECT * FROM store.feature_link WHERE (%s::text IS NULL OR status = %s)
           AND (%s::text IS NULL OR relation = %s) ORDER BY created_at LIMIT %s""",
        (status, status, relation, relation, limit)).fetchall()


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
