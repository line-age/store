# Building store

How `store` gets from a drafted model to the thing the loop runs on (controlplane L-0013).
The model itself is in [model.md](model.md); this is the order of work, what each step
delivers, and the choices that have to be made along the way.

## The first finish line

The natural gas traces round-trip:

1. `backport` reads the stitched natural gas GeoPackage and writes it to `store` through
   the API.
2. QGIS opens the PostGIS views directly, with the Temporal Controller working, and shows
   the same lines appearing and disappearing in the same years as the GeoPackage.
3. `release` exports a GeoPackage from `store` that matches the input as traced: same
   lines, same geometry, same start dates. The proposed ends are left out until someone
   confirms them, because a release ignores anything still proposed (scenarios, case 2).
4. Every row written in (1) has an audit entry naming who wrote it.

That is the controlplane's "QGIS round trip against the hosted database", made concrete
with real data. Everything below is in service of it, and anything not needed for it waits.

## In scope for v0, and not

**In:** `FEATURE` (kinds `line`, `section`), `CLAIM`, `ATTRIBUTE`, `CITATION`,
`CLAIM_CITATION`, `SOURCE`, `AGENT`, `REVIEW`, `FEATURE_LINK`, history tables, and a
citation request record. The QGIS views. The write and read API.

**Later:** `NODE`, `EDGE`, `EDGE_SECTION` and `STATION`. The natural gas traces show lines
only, and I-0007 says not to invent nodes, so topology waits for a source that shows it.
The tables can be in the first migration, but nothing writes to them yet and nothing in v0
depends on them.

## Model changes the traces need

Working through the natural gas import against [model.md](model.md) turned up these. Each
is small; all of them should go into `model.md` before the first migration.

- **`SOURCE.as_of_edtf`.** Most of the sheets state a data cutoff ("as of June 30, 1955")
  that differs from the imprint. The map-date rule (I-0003) uses the cutoff when there is
  one. `published_edtf` stays as the imprint.
- **`CLAIM_CITATION.role` gains `map_omits`.** A later sheet that no longer shows a line is
  evidence that it ended, and weaker evidence than a passage saying so. It needs its own
  role so nobody mistakes it for `states`.
- **`FEATURE_LINK` gains `overlaps`, `score` and `asserted_by`.** A removal trace that
  covers 40% of a section is not `same_as`. The score is the proposer's confidence that
  the match is right (not how much of the section it covers, which is computed from the
  geometry), and the proposer is an agent like any other.
- **An external key table.** `(system, key) -> feature or claim`, for example
  `("natural_gas_traces", "<period>/<file>/<layer>/<fid>")`. It makes `backport` re-runnable:
  a second run updates instead of duplicating. ULIDs from the stitched GeoPackage convert
  to UUIDs directly, so the existing ids can be kept.
- **`CITATION_REQUEST`.** Which section, which attributes (or anything), who asked (a
  person or a bulk job), priority (`interactive | background`), status
  (`queued | running | done | failed`), and the run details (start, finish, model, cost).
  "Ask AI" in `review` creates one and has `cite` process it at once; a bulk job creates
  many and `cite` works through them. Both write candidates straight to the claim tables,
  and each candidate claim points back to its request (`CLAIM.request_id`). It belongs in
  v0 because the loop can't run without it, even though nothing uses it until winter.

## How the GeoPackage maps onto the model

So the model can be checked against it before any code exists.

| In the GeoPackage | In `store` |
| --- | --- |
| one `sheets` row | `SOURCE` of kind `map_sheet`, with `as_of_edtf`; its scan files as identifiers |
| one traced line (`base` or `add`) | `FEATURE` (section) with a `route` claim and a `commodity` claim |
| the sheet it first appears on | `CITATION` to that sheet, role `map_shows`, on both claims |
| `existed_from` (`../1953-12-31`) | a `status: operational` claim, `valid_edtf` as written |
| the tracer code | `AGENT` (person) who asserted the `route` claim |
| one removal (`sub`) trace | its own `FEATURE` (section): `route` claim cited to the earlier sheet (`map_shows`), and an end of `status` cited to the later sheet (`map_omits`) |
| a proposed match, removal to section | `FEATURE_LINK` `same_as` or `overlaps`, status `proposed`, with the score, asserted by the stitch script as a pipeline agent |
| the stitch script's split pieces | not stored while the match is proposed; the QGIS views draw them from the link, and `release` ignores proposed links. Confirming an `overlaps` link splits the section for real |
| `source`, `source_layer`, `source_fid` | external keys |

The split pieces are the interesting call. The stitch script split 48 traced lines into 94
pieces so QGIS could show part of a line ending. Where it cut is wherever its 1 km buffer
happened to end; no sheet shows a break there. Storing those pieces as sections would
record a judgement (which part of the line the removal meant) as if it were a trace, and
undoing it after a rejected match means gluing a line back together.

So the split happens in two steps (agreed 2026-11-11):

1. **Proposed.** `store` keeps the line and the removal exactly as traced, plus one
   `overlaps` link between them with its score. The QGIS views draw the cut from the link,
   marked as proposed, so QGIS still shows part of the line ending; `release` ignores
   proposed links, as it leaves out candidate claims. Rejecting the link leaves nothing to
   undo.
2. **Confirmed.** When a person confirms the link in `review`, the cut becomes real: the
   section is split into two new sections (the research partner's 1a and 1b), joined to the
   original by `split_into` links, each with its own `route` claim. The ended piece takes
   the removal's end of `status`; the other carries on. The person can move the cut before
   confirming, and the confirmation is in the audit trail like any other review.

A `same_as` link (the removal covers the whole line) needs no split; confirming it just
puts the end on the line.

Two of the research partner's answers change this mapping, and the email asking them is
out: what a `sub` file means, and which of two same-year sheets was traced. See
controlplane open questions.

## Stack

Agreed 2026-11-11.

- **Postgres 16 and PostGIS 3.4.** Already decided.
- **Migrations in plain SQL** with a small runner (dbmate or sqitch). The schema leans on
  triggers and views, which ORMs handle badly, and other repos must never depend on the
  migrations anyway (L-0002).
- **The API in Python**, FastAPI and psycopg 3. `cite`, `trace` and `backport` are Python,
  and the GIS libraries they use are too, so one language keeps the published model package
  usable everywhere.
- **The model package** is a top-level `model/` directory: the attribute registry, the
  enums and typed request and response shapes, published as its own versioned package
  (L-0002's rule for a cheap split later).
- **Auth.** GitHub login gated by team for people, the same rule as `georef` (L-0012).
  Tokens for pipelines, each bound to a pipeline `AGENT`.

## The audit trail

It lives in the database, because QGIS writes to PostGIS directly (controlplane, store
open questions).

- Every table has a history table filled by trigger: old row, new row, operation,
  transaction id, timestamp and agent.
- The agent comes from a session setting (`lineage.agent_id`) that the API sets per
  request. For a direct QGIS connection, each person gets their own database role, and a
  role-to-agent table resolves it.
- A write with no resolvable agent is refused. An anonymous edit is worse than no edit.

## QGIS views

A `qgis` schema of read views, so QGIS users never see the claim tables unless they want
to:

- `qgis.sections`: one row per section, geometry from its current `route` claim, and the
  same date columns the GeoPackage has: `existed_from` and `existed_to` as EDTF,
  `time_start` and `time_end` collapsed per Legend 0011, plus claim and review status.
- `qgis.removals`, and `qgis.proposed_links` drawn as lines between the two features.
- A QGIS project stored in the database (QGIS can save projects to Postgres) with the
  temporal settings already on, so opening it is one step.

Editing through these views, for example correcting a route in QGIS, is a later question.
The views are read-only in v0.

## The API, v0

Small and boring on purpose.

- **Write bundles.** One call creates a source, a citation and the claims it supports, in
  one transaction, because a claim without its citation must never exist even briefly
  (I-0002).
- **Features.** Create; read by bbox, time and network; read one with all its claims,
  citations, reviews and links.
- **Reviews.** Accept, correct or reject a claim. Confirm or reject a link.
- **Citation requests.** Create (interactive or background), list by status, take the next
  one (for `cite`'s background workers), and complete.
- **Missing.** "Sections with no `diameter` claim", "proposed links not yet reviewed". This
  is what `review` filters on, and what tells a person where the loop is done.

## Milestones

Rough sizes for one developer; a guess, not a commitment.

| | What | Delivers | Size |
| --- | --- | --- | --- |
| M0 | Settle the model | the changes above in `model.md`; merge the model draft | days |
| M1 | Schema | migrations, history triggers, agent resolution, Compose with PostGIS, CI, a synthetic fixture | 1 week |
| M2 | QGIS views | the `qgis` schema and a stored project; the fixture animates in QGIS | days |
| M3 | API | the endpoints above, auth, the model package published | 1-2 weeks |
| M4 | Backport the natural gas traces | in the `backport` repo; real data in a local `store` | 1 week |
| M5 | Round trip | `release` v0 export, the comparison test, a hosted database | days |
| M6 | Loop hooks | citation requests with `cite`, interactive and background; read endpoints `review` needs | 1 week |

M4 can start against M1's schema with direct SQL if the API is late; switching it to the API
is a small change, and it is better to find model problems in week two than week five.

## Tests and data

Fixtures in this repo or `deploy` are synthetic (L-0009). The natural gas data is the
research partner's and stays outside this org; the round trip test runs where that data
lives, against a path it is given.

## Decisions

1. **Stack:** as above. Agreed 2026-11-11.
2. **QGIS editing through the views:** not a v0 goal; the API first. Agreed 2026-11-11.
3. **Split pieces:** links only while a match is proposed; the split is made when a person
   confirms it. See "How the GeoPackage maps onto the model". Agreed 2026-11-11.
