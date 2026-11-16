"""The store API, v0 (docs/plan.md, "The API, v0"). Small and boring on purpose."""


import datetime as dt
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from lineage_model import (
    AgentIn,
    Bundle,
    BundleResult,
    CitationRequestDone,
    CitationRequestIn,
    ClaimReviewIn,
    LinkReviewIn,
)

from . import reads, writes
from .auth import Authenticator, Principal
from .config import Settings
from .db import Database


def create_app(settings: Settings | None = None, authenticator: Authenticator | None = None) -> FastAPI:
    settings = settings or Settings()
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_url)
        state["db"] = db
        state["auth"] = authenticator or Authenticator(db, settings.github_teams, settings.github_api,
                                                       settings.auth_cache_seconds)
        if authenticator is not None:
            authenticator.db = db
        yield
        db.close()

    app = FastAPI(title="Lineage store", version="0.1.0", lifespan=lifespan)

    def db() -> Database:
        return state["db"]

    def who(request: Request) -> Principal:
        return state["auth"](request)

    Who = Annotated[Principal, Depends(who)]
    Db = Annotated[Database, Depends(db)]

    @app.exception_handler(psycopg.errors.RaiseException)
    async def _rule(_, exc):  # a rule the database enforces (triggers)
        return JSONResponse(status_code=422, content={"detail": exc.diag.message_primary})

    @app.exception_handler(psycopg.errors.IntegrityError)
    async def _integrity(_, exc):
        return JSONResponse(status_code=422, content={"detail": exc.diag.message_primary})

    @app.get("/healthz")
    def healthz(d: Db):
        with d.read() as conn:
            conn.execute("SELECT 1")
        return {"ok": True}

    @app.get("/me")
    def me(w: Who):
        return w

    # Writes -----------------------------------------------------------------------------

    @app.post("/bundles", response_model=BundleResult)
    def post_bundle(b: Bundle, w: Who, d: Db):
        with d.write(w.agent_id) as conn:
            return writes.apply_bundle(conn, w, b)

    @app.post("/agents")
    def post_agent(a: AgentIn, w: Who, d: Db):
        with d.write(w.agent_id) as conn:
            aid, created = writes.upsert_agent(conn, w, a)
        return {"id": aid, "created": created}

    @app.post("/claims/{claim_id}/reviews")
    def post_claim_review(claim_id: uuid.UUID, r: ClaimReviewIn, w: Who, d: Db):
        with d.write(w.agent_id) as conn:
            return writes.review_claim(conn, w, claim_id, r)

    @app.post("/links/{link_id}/reviews")
    def post_link_review(link_id: uuid.UUID, r: LinkReviewIn, w: Who, d: Db):
        with d.write(w.agent_id) as conn:
            return writes.review_link(conn, w, link_id, r)

    @app.post("/citation-requests")
    def post_request(r: CitationRequestIn, w: Who, d: Db):
        with d.write(w.agent_id) as conn:
            return writes.create_request(conn, w, r)

    @app.post("/citation-requests/next")
    def next_request(w: Who, d: Db):
        with d.write(w.agent_id) as conn:
            req = writes.take_request(conn, w)
            if req is None:
                return Response(status_code=204)
            return reads.request_context(conn, req)

    @app.post("/citation-requests/{request_id}/complete")
    def complete_request(request_id: uuid.UUID, r: CitationRequestDone, w: Who, d: Db):
        with d.write(w.agent_id) as conn:
            return writes.finish_request(conn, request_id, r)

    # Reads ------------------------------------------------------------------------------

    @app.get("/sections")
    def get_sections(w: Who, d: Db, bbox: str | None = None, at: dt.date | None = None,
                     network: str | None = None, limit: int = Query(1000, le=10000)):
        box = None
        if bbox:
            try:
                box = tuple(float(x) for x in bbox.split(","))
                assert len(box) == 4
            except (ValueError, AssertionError):
                raise HTTPException(422, "bbox is minx,miny,maxx,maxy in WGS 84") from None
        with d.read() as conn:
            return reads.sections(conn, box, at, network, limit)

    @app.get("/features/{feature_id}")
    def get_feature(feature_id: uuid.UUID, w: Who, d: Db):
        with d.read() as conn:
            return reads.feature(conn, feature_id)

    @app.get("/missing")
    def get_missing(attribute: str, w: Who, d: Db, network: str | None = None,
                    limit: int = Query(1000, le=10000)):
        with d.read() as conn:
            return reads.missing(conn, attribute, network, limit)

    @app.get("/links")
    def get_links(w: Who, d: Db, status: Literal["proposed", "confirmed", "rejected"] | None = None,
                  relation: str | None = None, limit: int = Query(1000, le=10000)):
        with d.read() as conn:
            return reads.links(conn, status, relation, limit)

    @app.get("/citation-requests")
    def get_requests(w: Who, d: Db, status: str | None = None, limit: int = Query(100, le=1000)):
        with d.read() as conn:
            return reads.requests(conn, status, limit)

    return app
