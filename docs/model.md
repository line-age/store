# The store model

The first migration (`db/migrations/20261112000001_core.sql`) implements this. Change it
here first, then in a new migration.

## Principles

- **A feature is an identity, nothing more.** Lines, sections, stations, nodes and edges are
  rows with an id. Everything a source says about one is a claim.
- **There are no fixed attributes.** Name, operator, commodity, route and diameter can all
  change. "Fixed" only means we have seen one value so far.
- **Cardinality is per instant, not over all time.** `diameter` is `one`: at most one value
  is true at any moment, but it can change in 1920. `serves` is `many`. Two `one` claims with
  overlapping validity are a disagreement, and both are kept.
- **One citation supports many claims.** A trade-press passage stating a diameter, an
  operator and a new station is one citation and several claims.
- **Facts and judgements are separate (I-0005).** Citations and the claims they support are
  what sources said. Identity (`FEATURE_LINK`) and topology (`NODE`, `EDGE`) are our
  construction, built from accepted claims.
- **The research partner's one-row-per-section-per-year table is an output.** `release`
  builds it as a view. Storage does not look like it.

## ERD

```mermaid
erDiagram
    FEATURE ||--o| LINE : "is a"
    FEATURE ||--o| SECTION : "is a"
    FEATURE ||--o| STATION : "is a"
    FEATURE ||--o| NODE : "is a"
    FEATURE ||--o| EDGE : "is a"
    NODE ||--o{ EDGE : "from / to"
    NODE |o--o{ STATION : "sits at"
    EDGE ||--o{ EDGE_SECTION : ""
    SECTION ||--o{ EDGE_SECTION : ""

    FEATURE ||--o{ CLAIM : "described by"
    ATTRIBUTE ||--o{ CLAIM : "types"
    CLAIM }o--o| FEATURE : "value_feature_id"
    CLAIM ||--o{ CLAIM_CITATION : ""
    CITATION ||--o{ CLAIM_CITATION : "supports"
    SOURCE ||--o{ CITATION : "cited at"
    SOURCE |o--o{ SOURCE : "contains"
    AGENT ||--o{ CLAIM : "asserted"
    CITATION_REQUEST |o--o{ CLAIM : "produced"
    FEATURE ||--o{ CITATION_REQUEST : "asked about"
    AGENT ||--o{ CITATION_REQUEST : "asked"
    CLAIM ||--o{ REVIEW : ""
    REVIEW |o--o| CLAIM : "replaced_by"
    AGENT ||--o{ REVIEW : "reviewed"
    FEATURE ||--o{ FEATURE_LINK : ""

    FEATURE {
        uuid id PK
        text kind "line | section | station | node | edge"
        text network "gas | oil | refined | electric | road | water"
        text label "display only, never evidence"
    }
    LINE {
        uuid feature_id PK
    }
    SECTION {
        uuid feature_id PK
    }
    STATION {
        uuid feature_id PK
        uuid node_id FK "nullable"
    }
    NODE {
        uuid feature_id PK
        text kind "junction | end_node"
        geometry geom "Point"
    }
    EDGE {
        uuid feature_id PK
        uuid from_node_id FK
        uuid to_node_id FK
        geometry geom "LineString, built from accepted route claims"
    }
    EDGE_SECTION {
        uuid edge_id FK
        uuid section_id FK
    }
    ATTRIBUTE {
        text key PK
        text value_type "text | quantity | enum | geometry | feature"
        text cardinality "one | many, at a single instant"
        text_arr feature_kinds
        text_arr networks
        text_arr enum_values "enum attributes"
    }
    CLAIM {
        uuid id PK
        uuid feature_id FK
        text attribute_key FK
        text value_text
        numeric value_num
        text value_unit "as written: in, kV"
        geometry value_geom
        uuid value_feature_id FK
        text valid_edtf "as the source said it"
        date start_earliest
        date start_latest
        date end_earliest
        date end_latest
        text status "candidate | accepted | rejected"
        uuid asserted_by FK
        uuid request_id FK "the citation request that produced it, if any"
    }
    CITATION_REQUEST {
        uuid id PK
        uuid feature_id FK
        text_arr attribute_keys "empty = anything"
        uuid requested_by FK "a person, or a bulk job's agent"
        text priority "interactive | background"
        text status "queued | running | done | failed"
        timestamptz started_at
        timestamptz finished_at
        text run "model, version, cost"
    }
    CITATION {
        uuid id PK
        uuid source_id FK
        text locator "vol, no, page / sheet region"
        text quote
        text url
        text archive_url
    }
    CLAIM_CITATION {
        uuid claim_id FK
        uuid citation_id FK
        text role "states | map_shows | map_omits | confirms"
        numeric confidence
    }
    SOURCE {
        uuid id PK
        uuid parent_id FK "issue in journal, sheet in atlas"
        text kind "periodical | issue | map_sheet | filing | dataset | web"
        text title
        text published_edtf "the imprint"
        text as_of_edtf "the data cutoff, when stated (I-0003)"
        text identifier "IA item, IIIF manifest"
        text annotation_uri "map sheets: georef annotation"
        text lane "license / retrieval path (I-0006)"
    }
    AGENT {
        uuid id PK
        text kind "person | pipeline"
        text github_login
        text pipeline "name, version, run"
    }
    REVIEW {
        uuid id PK
        uuid claim_id FK "a claim or a link, not both"
        uuid link_id FK
        uuid reviewer_id FK
        text verdict "claims: accept | reject | correct; links: confirm | reject"
        uuid replaced_by_claim_id FK "correct only: the reviewer's new claim"
        text note
    }
    FEATURE_LINK {
        uuid id PK
        uuid from_feature_id FK
        uuid to_feature_id FK
        text relation "same_as | overlaps | split_into | part_of | parallel_to"
        text status "proposed | confirmed | rejected"
        numeric score "the proposer's confidence, nullable"
        uuid asserted_by FK "AGENT"
        uuid citation_id FK "optional"
    }
```

`FEATURE_LINK.relation` fixes which feature kinds each end may be, checked in the database:

| relation | from | to | meaning |
| --- | --- | --- | --- |
| `same_as` | section | section | the same pipe, traced twice |
| `overlaps` | section | section | a removal covers part of this section |
| `split_into` | section | section | this section was cut into these pieces |
| `part_of` | section | line | this section belongs to this line |
| `parallel_to` | section | section | a second pipe beside this one, not the same pipe |

Not drawn:

- **History.** One `audit.history` table, filled by a trigger on every table: old row, new
  row, operation, transaction, time and agent. A write with no agent is refused, so writes
  straight from QGIS are audited too.
- **`EXTERNAL_KEY`**: `(system, key) -> (entity, id)`, so an importer can run twice without
  duplicating anything.
- **`AGENT_TOKEN`** (pipeline API tokens, hashed; some may assert claims for another agent,
  for importers) and **`AGENT_ROLE`** (a database role used from QGIS -> its person).
- **Rules the database enforces:** a claim's value fits its attribute; a claim has a
  citation by commit time (I-0002); claims and links are never edited, only their status
  moves; a link's ends are the feature kinds its relation allows.

## Attributes (starting set)

| Key | Applies to | Type | Cardinality |
| --- | --- | --- | --- |
| `name` | line, section, station | text | one |
| `owner` | line, section, station | text | many (joint ownership) |
| `operator` | line, section, station | text | one |
| `commodity` | line, section | enum, no allowed values yet (see below) | one |
| `status` | all | enum: proposed, permitted, under_construction, operational, maintenance, decommissioned | one |
| `route` | section | geometry (LineString) | one |
| `location` | station, node | geometry (Point) or place text | one |
| `diameter` | section (pipelines) | quantity | one |
| `voltage` | section (electric) | quantity | one |
| `gauge`, `lanes` | section (road, rail) | quantity | one |
| `station_kind` | station | enum: pump, compressor, substation, terminal, intake, delivery | one |
| `serves` | station | feature (section) | many |
| `flow_direction` | edge | enum: forward, reverse, both (relative to from/to) | one |

`commodity` has no allowed values yet, so the store refuses every commodity claim. A
feature's `network` already says what it carries in nearly every case; the attribute stays
on the list so we can give it values when we need one. If we do, it is for
what the network doesn't distinguish: natural versus manufactured gas on a gas line (town
gas lines were converted in the 1940s and 50s, which would be two dated claims), or crude
versus products on an oil line.

Twinning and looping: a twin is its own section with its own build date, owner and
diameter, linked to the original by a `parallel_to` link (scenarios, case 10). `diameter` stays single-valued.

## Worked example

> Foo Bar Oil Co. announced that their 20" pipeline upgrade to the Kansas Pipeline is
> completed and began operation in October (1920) with a new pumping station in BFE, Kansas.

One `SOURCE` (the issue, child of the journal), one `CITATION` (page and quote), and these
claims, each linked to that citation:

| Feature | Attribute | Value | Valid |
| --- | --- | --- | --- |
| section S1 | `diameter` | 20 in | from 1920-10 |
| section S1 | `operator` | Foo Bar Oil Co. | at 1920-10 |
| station ST1 (new) | `location` | BFE, Kansas | from 1920-10 |
| station ST1 | `station_kind` | pump | from 1920-10 |
| station ST1 | `serves` | S1 | from 1920-10 |

An earlier citation supports S1 `diameter` 6 in for 1898..1920. The two don't conflict
because their intervals don't overlap.

## The research partner's columns

| Their column | Here |
| --- | --- |
| PipeID / PipeIDName | `LINE` + `name` claim; sections join it by `part_of` links |
| SectionID, a/b/c splits | `SECTION` + `FEATURE_LINK split_into` |
| ObjectID, Year | generated by the year-sliced view |
| YearConstructed / YearDecommissioned | derived from `status` claims |
| EndpointCoordinates, StatesCrossed | derived from `route` geometry |
| LineType | the feature's `network` (`commodity` is kept for finer distinctions, unused so far) |
| Ownership(s) | `owner` and `operator` |
| Size | `diameter` / `voltage` / `gauge` / `lanes`; twins are separate sections |
| Direction of flow | `flow_direction` on edges |
| Stations | `STATION` features + `serves` claims |
| MapFileBaselineTrace / MapFileConfirmationTrace | citations to a `map_sheet` source, role `map_shows` / `confirms` |
| Maps Data tab | `SOURCE` (map_sheet) with IIIF identifier and georef annotation |

## Open

- **Section to line membership** is a `part_of` link (settled in scenarios, case 9):
  proposed and confirmed like any other link, optionally cited. A section that moves to
  another line (a sale, absorption into another system) gets a new link; whether links
  need validity dates for that is still open.
- **Directionality through junctions** (flow from edge A may continue to B but not C). Not
  modeled yet; probably a turn table on `NODE`.
- **Nodes and I-0007.** Nodes are recorded only where a source shows one. An edge whose ends
  no source shows still needs end nodes; decide whether those are marked as inferred.
- **Edge geometry versus route claims.** Edges are built from accepted `route` claims.
  Decide what happens to an edge when a newer route claim is accepted.
- **Claim attachment as a judgement.** Under I-0005, which section a passage is about is a
  judgement. Today that judgement is `CLAIM.feature_id`, reassigned by audited update.
