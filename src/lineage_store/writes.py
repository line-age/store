"""Everything that changes the store. Each function runs inside one transaction whose agent
is already set (Database.write), so the audit trail names the caller."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import HTTPException
from lineage_model import (
    AgentIn,
    Bundle,
    BundleResult,
    CitationRequestDone,
    CitationRequestIn,
    ClaimIn,
    ClaimReviewIn,
    ExternalKey,
    LinkReviewIn,
)
from psycopg import Connection

from .auth import Principal

GEOM = "ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)"


def _geojson(g: dict[str, Any] | None) -> str | None:
    return json.dumps(g) if g is not None else None


def _existing(conn: Connection, ek: ExternalKey | None, entity: str) -> uuid.UUID | None:
    if ek is None:
        return None
    row = conn.execute("SELECT entity, entity_id FROM store.external_key WHERE system = %s AND key = %s",
                       (ek.system, ek.key)).fetchone()
    if row and row["entity"] != entity:
        raise HTTPException(409, f"external key {ek.system}/{ek.key} names a {row['entity']}, not a {entity}")
    return row["entity_id"] if row else None


def _remember(conn: Connection, ek: ExternalKey | None, entity: str, entity_id: uuid.UUID) -> None:
    if ek is not None:
        conn.execute("INSERT INTO store.external_key (system, key, entity, entity_id) VALUES (%s, %s, %s, %s)",
                     (ek.system, ek.key, entity, entity_id))


def _agent_kind(conn: Connection, agent_id: uuid.UUID) -> str:
    row = conn.execute("SELECT kind FROM store.agent WHERE id = %s", (agent_id,)).fetchone()
    if not row:
        raise HTTPException(422, f"no agent {agent_id}")
    return row["kind"]


def _asserter(conn: Connection, who: Principal, asserted_by: uuid.UUID | None) -> tuple[uuid.UUID, str]:
    agent = asserted_by or who.agent_id
    if agent != who.agent_id and not who.may_assert_for_others:
        raise HTTPException(403, "this token may not assert claims or links for another agent")
    return agent, _agent_kind(conn, agent)


# Bundles ------------------------------------------------------------------------------------

def apply_bundle(conn: Connection, who: Principal, b: Bundle) -> BundleResult:
    ids: dict[str, uuid.UUID] = {}
    created: dict[str, bool] = {}

    def ref_of(row, section: str, i: int) -> str:
        return row.ref or f"{section}[{i}]"

    def resolve(ref: str | None) -> uuid.UUID | None:
        if ref is None:
            return None
        if ref in ids:
            return ids[ref]
        try:
            return uuid.UUID(ref)
        except ValueError:
            raise HTTPException(422, f"unknown ref {ref!r}: not earlier in this bundle and not a UUID") from None

    def done(ref: str, rid: uuid.UUID, new: bool) -> None:
        if ref in ids:
            raise HTTPException(422, f"ref {ref!r} is used twice")
        ids[ref], created[ref] = rid, new

    for i, s in enumerate(b.sources):
        ref = ref_of(s, "sources", i)
        if (rid := _existing(conn, s.external_key, "source")):
            done(ref, rid, False)
            continue
        rid = conn.execute(
            """INSERT INTO store.source (parent_id, kind, title, published_edtf, as_of_edtf, identifier,
                                         annotation_uri, lane)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (resolve(s.parent), s.kind, s.title, s.published_edtf, s.as_of_edtf, s.identifier,
             s.annotation_uri, s.lane)).fetchone()["id"]
        _remember(conn, s.external_key, "source", rid)
        done(ref, rid, True)

    for i, c in enumerate(b.citations):
        ref = ref_of(c, "citations", i)
        if (rid := _existing(conn, c.external_key, "citation")):
            done(ref, rid, False)
            continue
        rid = conn.execute(
            """INSERT INTO store.citation (source_id, locator, quote, url, archive_url)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (resolve(c.source), c.locator, c.quote, c.url, c.archive_url)).fetchone()["id"]
        _remember(conn, c.external_key, "citation", rid)
        done(ref, rid, True)

    for i, f in enumerate(b.features):
        ref = ref_of(f, "features", i)
        if (rid := _existing(conn, f.external_key, "feature")):
            done(ref, rid, False)
            continue
        rid = conn.execute(
            "INSERT INTO store.feature (id, kind, network, label) VALUES (coalesce(%s, gen_random_uuid()), %s, %s, %s) "
            "RETURNING id", (f.id, f.kind, f.network, f.label)).fetchone()["id"]
        _remember(conn, f.external_key, "feature", rid)
        done(ref, rid, True)

    for i, c in enumerate(b.claims):
        ref = ref_of(c, "claims", i)
        if (rid := _existing(conn, c.external_key, "claim")):
            done(ref, rid, False)
            continue
        rid = insert_claim(conn, who, c, resolve)
        _remember(conn, c.external_key, "claim", rid)
        done(ref, rid, True)

    for i, link in enumerate(b.links):
        ref = ref_of(link, "links", i)
        if (rid := _existing(conn, link.external_key, "link")):
            done(ref, rid, False)
            continue
        agent, kind = _asserter(conn, who, link.asserted_by)
        if link.status != "proposed" and kind != "person":
            raise HTTPException(422, "only a person confirms or rejects a link (I-0004)")
        rid = conn.execute(
            """INSERT INTO store.feature_link (from_feature_id, to_feature_id, relation, status, score,
                                               asserted_by, citation_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (resolve(link.from_), resolve(link.to), link.relation, link.status, link.score, agent,
             resolve(link.citation))).fetchone()["id"]
        _remember(conn, link.external_key, "link", rid)
        done(ref, rid, True)

    return BundleResult(ids=ids, created=created)


def insert_claim(conn: Connection, who: Principal, c: ClaimIn, resolve) -> uuid.UUID:
    agent, kind = _asserter(conn, who, c.asserted_by)
    status = c.status or ("accepted" if kind == "person" else "candidate")
    if status == "accepted" and kind != "person":
        raise HTTPException(422, "a machine's claim starts as a candidate; a person accepts it (I-0004)")
    rid = conn.execute(
        f"""INSERT INTO store.claim (feature_id, attribute_key, value_text, value_num, value_unit, value_geom,
                                    value_feature_id, valid_edtf, start_earliest, start_latest, end_earliest,
                                    end_latest, status, asserted_by, request_id)
            VALUES (%s, %s, %s, %s, %s, {GEOM}, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (resolve(c.feature), c.attribute, c.value_text, c.value_num, c.value_unit, _geojson(c.value_geom),
         resolve(c.value_feature), c.valid_edtf, c.start_earliest, c.start_latest, c.end_earliest,
         c.end_latest, status, agent, c.request_id)).fetchone()["id"]
    for cc in c.citations:
        conn.execute("INSERT INTO store.claim_citation (claim_id, citation_id, role, confidence) "
                     "VALUES (%s, %s, %s, %s)", (rid, resolve(cc.citation), cc.role, cc.confidence))
    return rid


# Agents -------------------------------------------------------------------------------------

def upsert_agent(conn: Connection, who: Principal, a: AgentIn) -> tuple[uuid.UUID, bool]:
    """Find or create an agent. Used by importers to name the people whose work they carry
    (a tracer code that is also a GitHub user is one agent, not two). Running it again with
    a new name, email or GitHub login updates the agent."""
    if not who.may_assert_for_others:
        raise HTTPException(403, "only a token allowed to assert for others registers agents")
    by_key = _existing(conn, a.external_key, "agent")
    by_login = None
    if a.github_login:
        row = conn.execute("SELECT id FROM store.agent WHERE github_login = %s", (a.github_login,)).fetchone()
        by_login = row["id"] if row else None
    if by_key and by_login and by_key != by_login:
        raise HTTPException(409, f"{a.external_key.system}/{a.external_key.key} and @{a.github_login} "
                                 "are two different agents already")
    found = by_key or by_login
    if found:
        # the importer's list is where these come from: a corrected name or email replaces the old
        conn.execute("UPDATE store.agent SET name = %s WHERE id = %s AND name <> %s", (a.name, found, a.name))
        if a.email:
            conn.execute("UPDATE store.agent SET email = %s WHERE id = %s AND email IS DISTINCT FROM %s",
                         (a.email, found, a.email))
        if a.github_login and not by_login:
            conn.execute("UPDATE store.agent SET github_login = %s WHERE id = %s", (a.github_login, found))
        if a.external_key and not by_key:
            _remember(conn, a.external_key, "agent", found)
        return found, False
    rid = conn.execute("INSERT INTO store.agent (kind, name, github_login, email, pipeline) VALUES (%s, %s, %s, %s, %s) "
                       "RETURNING id", (a.kind, a.name, a.github_login, a.email, a.pipeline)).fetchone()["id"]
    _remember(conn, a.external_key, "agent", rid)
    return rid, True


# Reviews ------------------------------------------------------------------------------------

def _person(who: Principal) -> None:
    if who.kind != "person":
        raise HTTPException(403, "reviews are a person's call (I-0004)")


def review_claim(conn: Connection, who: Principal, claim_id: uuid.UUID, r: ClaimReviewIn) -> dict:
    _person(who)
    claim = conn.execute("SELECT * FROM store.claim WHERE id = %s FOR UPDATE", (claim_id,)).fetchone()
    if not claim:
        raise HTTPException(404, "no such claim")
    if claim["status"] == "rejected":
        raise HTTPException(409, "that claim is already rejected")
    if r.verdict == "accept" and claim["status"] != "candidate":
        raise HTTPException(409, "only a candidate can be accepted")
    replaced_by = None
    if r.verdict == "correct":
        rep = r.replacement
        cites = rep.citations
        if cites is None:
            rows = conn.execute("SELECT citation_id, role FROM store.claim_citation WHERE claim_id = %s",
                                (claim_id,)).fetchall()
            cites = [{"citation": str(x["citation_id"]), "role": x["role"]} for x in rows]
        new = ClaimIn(feature=str(claim["feature_id"]), attribute=claim["attribute_key"],
                      citations=cites, status="accepted",
                      **rep.model_dump(exclude={"citations", "value_feature"}),
                      value_feature=str(rep.value_feature) if rep.value_feature else None)
        replaced_by = insert_claim(conn, who, new, lambda x: uuid.UUID(x) if x else None)
    conn.execute("UPDATE store.claim SET status = %s WHERE id = %s",
                 ("accepted" if r.verdict == "accept" else "rejected", claim_id))
    rid = conn.execute(
        "INSERT INTO store.review (claim_id, reviewer_id, verdict, replaced_by_claim_id, note) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (claim_id, who.agent_id, r.verdict, replaced_by, r.note)).fetchone()["id"]
    return {"review_id": rid, "replaced_by_claim_id": replaced_by}


def review_link(conn: Connection, who: Principal, link_id: uuid.UUID, r: LinkReviewIn) -> dict:
    _person(who)
    link = conn.execute("SELECT * FROM store.feature_link WHERE id = %s FOR UPDATE", (link_id,)).fetchone()
    if not link:
        raise HTTPException(404, "no such link")
    if link["status"] != "proposed":
        raise HTTPException(409, f"that link is already {link['status']}")
    out: dict[str, Any] = {}

    def review(lid, verdict, note):
        return conn.execute("INSERT INTO store.review (link_id, reviewer_id, verdict, note) "
                            "VALUES (%s, %s, %s, %s) RETURNING id", (lid, who.agent_id, verdict, note)).fetchone()["id"]

    if r.verdict == "reject":
        conn.execute("UPDATE store.feature_link SET status = 'rejected' WHERE id = %s", (link_id,))
        out["review_id"] = review(link_id, "reject", r.note)
        return out

    if link["relation"] == "same_as" and r.keep == "from":
        # Keep the other trace's route: the confirmed link points from the trace set aside to
        # the one kept (scenarios, case 5), so it is written the other way round.
        new = conn.execute(
            """INSERT INTO store.feature_link (from_feature_id, to_feature_id, relation, status, score,
                                               asserted_by, citation_id)
               VALUES (%s, %s, 'same_as', 'confirmed', %s, %s, %s) RETURNING id""",
            (link["to_feature_id"], link["from_feature_id"], link["score"], who.agent_id,
             link["citation_id"])).fetchone()["id"]
        conn.execute("UPDATE store.feature_link SET status = 'rejected' WHERE id = %s", (link_id,))
        out["review_id"] = review(link_id, "reject", f"same pipe, other route kept: link {new}")
        out["confirmed_link_id"] = new
        return out

    conn.execute("UPDATE store.feature_link SET status = 'confirmed' WHERE id = %s", (link_id,))
    out["review_id"] = review(link_id, "confirm", r.note)
    if link["relation"] == "overlaps":
        out.update(_split(conn, who, link, r.cut))
    return out


def _split(conn: Connection, who: Principal, link: dict, cut: dict | None) -> dict:
    """Confirming an `overlaps` link cuts the section for real (scenarios, case 4): two new
    sections joined to it by `split_into`, each with its own route, and the removal joined
    to the piece that ended."""
    section, removal = link["to_feature_id"], link["from_feature_id"]
    route = conn.execute(
        "SELECT id, value_geom FROM store.claim WHERE feature_id = %s AND attribute_key = 'route' "
        "AND status = 'accepted' ORDER BY created_at DESC LIMIT 1", (section,)).fetchone()
    if not route:
        raise HTTPException(409, "the section has no accepted route to cut")
    if cut is not None:
        pieces = conn.execute(
            f"""WITH c AS (SELECT {GEOM} AS g)
                SELECT ST_AsGeoJSON(ST_Multi(ST_CollectionExtract(c.g, 2))) AS ended,
                       ST_AsGeoJSON(ST_Multi(ST_CollectionExtract(ST_Transform(ST_Difference(
                           ST_Transform(%s::geometry, 5070), ST_Buffer(ST_Transform(c.g, 5070), 1)), 4326), 2)))
                           AS rest
                FROM c""", (_geojson(cut), route["value_geom"])).fetchone()
    else:
        pieces = conn.execute(
            """SELECT ST_AsGeoJSON(ST_Multi(ST_CollectionExtract(store.near(s.g, r.value_geom), 2))) AS ended,
                      ST_AsGeoJSON(ST_Multi(ST_CollectionExtract(store.away(s.g, r.value_geom), 2))) AS rest
               FROM (SELECT %s::geometry AS g) s,
                    (SELECT value_geom FROM store.claim WHERE feature_id = %s AND attribute_key = 'route'
                     AND status = 'accepted' ORDER BY created_at DESC LIMIT 1) r""",
            (route["value_geom"], removal)).fetchone()
    feat = conn.execute("SELECT network FROM store.feature WHERE id = %s", (section,)).fetchone()
    cites = conn.execute("SELECT citation_id, role FROM store.claim_citation WHERE claim_id = %s",
                         (route["id"],)).fetchall()
    made = {}
    for which in ("ended", "rest"):
        geom = json.loads(pieces[which])
        if not geom.get("coordinates"):
            raise HTTPException(422, f"the cut leaves no {which} piece; confirm it as same_as instead")
        fid = conn.execute("INSERT INTO store.feature (kind, network) VALUES ('section', %s) RETURNING id",
                           (feat["network"],)).fetchone()["id"]
        insert_claim(conn, who, ClaimIn(
            feature=str(fid), attribute="route", value_geom=geom, status="accepted",
            citations=[{"citation": str(c["citation_id"]), "role": c["role"]} for c in cites]),
            lambda x: uuid.UUID(x) if x else None)
        conn.execute("INSERT INTO store.feature_link (from_feature_id, to_feature_id, relation, status, asserted_by) "
                     "VALUES (%s, %s, 'split_into', 'confirmed', %s)", (section, fid, who.agent_id))
        made[which] = fid
    conn.execute("INSERT INTO store.feature_link (from_feature_id, to_feature_id, relation, status, score, asserted_by) "
                 "VALUES (%s, %s, 'same_as', 'confirmed', %s, %s)",
                 (removal, made["ended"], link["score"], who.agent_id))
    return {"ended_section_id": made["ended"], "continuing_section_id": made["rest"]}


# Citation requests --------------------------------------------------------------------------

def create_request(conn: Connection, who: Principal, r: CitationRequestIn) -> dict:
    return conn.execute(
        "INSERT INTO store.citation_request (feature_id, attribute_keys, requested_by, priority) "
        "VALUES (%s, %s, %s, %s) RETURNING *", (r.feature_id, r.attribute_keys, who.agent_id, r.priority)).fetchone()


def take_request(conn: Connection, who: Principal) -> dict | None:
    """The next queued request, interactive first, for a cite worker. Two workers never get
    the same one."""
    if who.kind != "pipeline":
        raise HTTPException(403, "requests are taken by pipelines")
    return conn.execute(
        """UPDATE store.citation_request SET status = 'running', started_at = now()
           WHERE id = (SELECT id FROM store.citation_request WHERE status = 'queued'
                       ORDER BY priority = 'interactive' DESC, created_at
                       LIMIT 1 FOR UPDATE SKIP LOCKED)
           RETURNING *""").fetchone()


def finish_request(conn: Connection, request_id: uuid.UUID, d: CitationRequestDone) -> dict:
    row = conn.execute(
        """UPDATE store.citation_request SET status = %s, finished_at = now(), run = %s, error = %s
           WHERE id = %s AND status = 'running' RETURNING *""",
        (d.status, json.dumps(d.run) if d.run is not None else None, d.error, request_id)).fetchone()
    if not row:
        raise HTTPException(409, "no running request with that id")
    return row
