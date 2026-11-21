"""What the store API takes and returns.

Writes come as bundles: everything one source supports, in one transaction, so a claim
never exists without its citation (I-0002). Inside a bundle, rows refer to each other by a
local `ref` (any string); a row that already exists is referred to by its UUID. Any row can
carry an external key, which makes the write idempotent: a second bundle with the same key
reuses the existing row instead of creating another.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "AcceptedClaim",
    "AgentIn",
    "Bundle",
    "BundleResult",
    "CitationIn",
    "CitationRequestDone",
    "CitationRequestIn",
    "CitationRole",
    "ClaimCitationIn",
    "ClaimIn",
    "ClaimReviewIn",
    "ClaimStatus",
    "ExternalKey",
    "FeatureDetail",
    "FeatureIn",
    "FeatureKind",
    "LinkIn",
    "LinkRelation",
    "LinkReviewIn",
    "LinkStatus",
    "Network",
    "ReplacementIn",
    "SectionRow",
    "SourceIn",
    "SourceKind",
]

FeatureKind = Literal["line", "section", "station", "node", "edge"]
Network = Literal["gas", "oil", "refined", "electric", "road", "water"]
SourceKind = Literal["periodical", "issue", "map_sheet", "filing", "dataset", "web"]
CitationRole = Literal["states", "map_shows", "map_omits", "confirms"]
ClaimStatus = Literal["candidate", "accepted", "rejected"]
LinkRelation = Literal["same_as", "overlaps", "split_into", "part_of", "parallel_to"]
LinkStatus = Literal["proposed", "confirmed", "rejected"]

# A reference to another row: a local ref from the same bundle, or an existing row's UUID.
Ref = str


class ExternalKey(BaseModel):
    system: str
    key: str


class _Row(BaseModel):
    ref: str | None = None
    external_key: ExternalKey | None = None


class SourceIn(_Row):
    kind: SourceKind
    title: str
    parent: Ref | None = None
    published_edtf: str | None = None
    as_of_edtf: str | None = None
    identifier: str | None = None
    annotation_uri: str | None = None
    lane: str | None = None


class CitationIn(_Row):
    source: Ref
    locator: str | None = None
    quote: str | None = None
    url: str | None = None
    archive_url: str | None = None


class FeatureIn(_Row):
    id: uuid.UUID | None = None  # keep an id the data already has (e.g. a ULID from a GeoPackage)
    kind: FeatureKind
    network: Network
    label: str | None = None


class ClaimCitationIn(BaseModel):
    citation: Ref
    role: CitationRole
    confidence: float | None = None


class ClaimIn(_Row):
    feature: Ref
    attribute: str
    value_text: str | None = None
    value_num: float | None = None
    value_unit: str | None = None
    value_geom: dict[str, Any] | None = None  # GeoJSON geometry, WGS 84
    value_feature: Ref | None = None
    valid_edtf: str | None = None
    start_earliest: dt.date | None = None
    start_latest: dt.date | None = None
    end_earliest: dt.date | None = None
    end_latest: dt.date | None = None
    citations: list[ClaimCitationIn] = Field(min_length=1)
    # Who said it. Defaults to the writer. Naming someone else needs a token allowed to.
    asserted_by: uuid.UUID | None = None
    # Defaults: accepted when a person asserts it, candidate when a pipeline does.
    status: ClaimStatus | None = None
    request_id: uuid.UUID | None = None


class LinkIn(_Row):
    from_: Ref = Field(alias="from")
    to: Ref
    relation: LinkRelation
    status: LinkStatus = "proposed"
    score: float | None = Field(default=None, ge=0, le=1)
    citation: Ref | None = None
    asserted_by: uuid.UUID | None = None

    model_config = {"populate_by_name": True}


class Bundle(BaseModel):
    sources: list[SourceIn] = []
    citations: list[CitationIn] = []
    features: list[FeatureIn] = []
    claims: list[ClaimIn] = []
    links: list[LinkIn] = []


class BundleResult(BaseModel):
    ids: dict[str, uuid.UUID]          # ref -> id
    created: dict[str, bool]           # ref -> False when an external key matched an existing row


class AgentIn(BaseModel):
    kind: Literal["person", "pipeline"]
    name: str
    github_login: str | None = None
    email: str | None = None
    pipeline: str | None = None
    external_key: ExternalKey | None = None


class ReplacementIn(BaseModel):
    """The reviewer's own claim, for a `correct` verdict. Same feature and attribute as the
    claim corrected; citations default to the corrected claim's."""
    value_text: str | None = None
    value_num: float | None = None
    value_unit: str | None = None
    value_geom: dict[str, Any] | None = None
    value_feature: uuid.UUID | None = None
    valid_edtf: str | None = None
    start_earliest: dt.date | None = None
    start_latest: dt.date | None = None
    end_earliest: dt.date | None = None
    end_latest: dt.date | None = None
    citations: list[ClaimCitationIn] | None = None


class ClaimReviewIn(BaseModel):
    verdict: Literal["accept", "reject", "correct"]
    note: str | None = None
    replacement: ReplacementIn | None = None

    @model_validator(mode="after")
    def _replacement_iff_correct(self):
        if (self.verdict == "correct") != (self.replacement is not None):
            raise ValueError("a replacement comes with a correct verdict, and only then")
        return self


class LinkReviewIn(BaseModel):
    verdict: Literal["confirm", "reject"]
    note: str | None = None
    # same_as: which trace's route is kept (scenarios, case 5). Default: the link's `to`.
    keep: Literal["from", "to"] = "to"
    # overlaps: the part of the section that ended, as GeoJSON (scenarios, case 4). Default:
    # the part within 1 km of the removal, as the stitch script measured it.
    cut: dict[str, Any] | None = None


class CitationRequestIn(BaseModel):
    feature_id: uuid.UUID
    attribute_keys: list[str] = []  # empty: anything
    priority: Literal["interactive", "background"] = "background"


class CitationRequestDone(BaseModel):
    status: Literal["done", "failed"]
    run: dict[str, Any] | None = None
    error: str | None = None


# Reads -----------------------------------------------------------------------------------
# What the read endpoints return beyond what each row of the database holds. Extra fields
# are allowed: these describe what a client may rely on, not everything sent.


class AcceptedClaim(BaseModel):
    """One accepted claim with at least one citation, raw: a client words the value."""
    claim_id: uuid.UUID
    value_text: str | None = None
    value_num: float | None = None
    value_unit: str | None = None
    valid_edtf: str | None = None
    created_at: dt.datetime
    # The earliest date of a map sheet cited as showing the line, as the sheet gives it.
    shown_as_of: str | None = None
    # Set when the claim is another feature's, reached through a confirmed link.
    inherited_from: uuid.UUID | None = None


class SectionRow(BaseModel, extra="allow"):
    """The properties of one `/sections` row: one drawn section, or one proposed piece of it.
    `row_id` is the section's id, or for a piece the id of the link that proposes the cut."""
    row_id: uuid.UUID
    section_id: uuid.UUID
    label: str | None = None
    network: str | None = None
    time_end: dt.date | None = None
    end_status: Literal["confirmed", "proposed"] | None = None
    line_id: uuid.UUID | None = None
    # attribute key -> accepted claims, newest first; the section's own and those it has
    # through confirmed links (store's scope)
    accepted: dict[str, list[AcceptedClaim]]
    # attribute key -> how many candidate claims wait on it: only the section's own, only
    # those with a value and at least one citation. Use this one; `has_candidates`, which
    # QGIS shows, counts any candidate and so can be true where this is empty.
    candidates: dict[str, int]
    # the years of the map sheets that show the section's route, oldest first
    shown_years: list[int]
    # the year of the sheet that no longer shows it, once a person confirmed its removal
    gone_year: int | None = None
    created_at: dt.datetime | None = None
    # where a proposed cut makes each row of the section a part of it: the route as traced
    whole_route: dict[str, Any] | None = None


class FeatureDetail(BaseModel, extra="allow"):
    """One feature as `/features/{id}` and `/features?ids=` give it."""
    id: uuid.UUID
    kind: FeatureKind
    claims: list[dict[str, Any]]
    # claims of other features reached through confirmed links, each with `inherited_from`
    inherited_claims: list[dict[str, Any]]
    links: list[dict[str, Any]]
