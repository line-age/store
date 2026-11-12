# store

Lineage network + evidence model, PostGIS database, and audited CRUD API.

Part of [Lineage](https://github.com/line-age/controlplane). What this component takes in,
what it puts out, and what it depends on are described in the controlplane's
[components.md](https://github.com/line-age/controlplane/blob/main/components.md#store).

Work is tracked in the [Store](https://github.com/orgs/line-age/projects/4) project. The
team lead is [@nickmarden](https://github.com/nickmarden). If that changes, the
[controlplane's table](https://github.com/line-age/controlplane#projects-and-leads) is updated first.

## What's here

| Path | What |
| --- | --- |
| [docs/model.md](docs/model.md) | the model: features, claims, citations, sources, agents, reviews, links |
| [docs/scenarios.md](docs/scenarios.md) | worked cases, from one section up; the tests follow them |
| [docs/plan.md](docs/plan.md) | the build-out plan and its decisions |
| `db/migrations/` | the schema, in plain SQL for [dbmate](https://github.com/amacneil/dbmate) |
| `model/` | `lineage-model`, the package other repos depend on: attribute registry, date helpers, API shapes |
| `src/lineage_store/` | the API (FastAPI, psycopg 3) and the `lineage-store` admin command |
| `tests/` | the scenarios and rules, against a real PostGIS |

Other repos depend on `lineage-model` or the API, never on the migrations (controlplane
L-0002).

## Running it

```sh
docker compose up --build          # PostGIS on :54329, migrations, the API on :8000
```

Without Compose: a PostGIS 16 database, then

```sh
export DATABASE_URL=postgresql://lineage:lineage@localhost:54329/lineage
dbmate --url "$DATABASE_URL?sslmode=disable" --migrations-dir db/migrations --no-dump-schema up
uv run lineage-store serve
```

## Who can write

Every write names an agent, or the database refuses it.

- **Pipelines** use a token: `uv run lineage-store token create backport --pipeline backport@<sha>
  --assert-for-others`. The token is printed once. `--assert-for-others` lets an importer
  write claims asserted by the people whose work it carries.
- **People** send a GitHub token. They must be in one of `LINEAGE_GITHUB_TEAMS`
  (`org/team-slug`, comma-separated); their agent is created on first sight, matched by
  GitHub login.
- **QGIS against PostGIS directly**: give each person a database role, and
  `uv run lineage-store role map <role> --github <login>`.

## The API

| | |
| --- | --- |
| `POST /bundles` | sources, citations, features, claims and links in one transaction, with local refs and optional external keys |
| `POST /agents` | find or create an agent (importers) |
| `POST /claims/{id}/reviews` | accept, reject, or correct with a replacement claim |
| `POST /links/{id}/reviews` | confirm or reject; confirming `overlaps` splits the section |
| `POST /citation-requests`, `POST /citation-requests/next`, `POST /citation-requests/{id}/complete` | Ask AI and bulk requests, and `cite`'s queue |
| `GET /sections` | GeoJSON from `qgis.sections`, by bbox, date and network |
| `GET /features/{id}` | a feature with its claims, citations, reviews, links and dates |
| `GET /missing?attribute=diameter` | drawn sections with no accepted claim for an attribute |
| `GET /links?status=proposed` | links waiting for a person |

OpenAPI docs are at `/docs` while it runs.

## QGIS

Connect to the database and add the views in the `qgis` schema: `sections` (with
`time_start` and `time_end` for the Temporal Controller), `removals`, `proposed_links`,
`lines` and `rejected_traces`. Proposed ends are drawn and marked `end_status = proposed`.

## Tests

```sh
docker compose up -d db
uv run pytest
```

Each run makes a fresh database on the server at `LINEAGE_TEST_ADMIN_URL` (default: the
Compose database) and drops it afterwards. All test data is made up (L-0009).
