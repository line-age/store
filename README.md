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

Everything runs as a container; the same images are what a Kubernetes deployment runs
(the `deploy` repo packages them).

```sh
docker compose up -d --build       # PostGIS on :47119, migrations, the QGIS project, the API on :47110
```

The host ports are Lineage's fixed local ports ([controlplane ports.md](https://github.com/line-age/controlplane/blob/main/ports.md));
`LINEAGE_API_PORT` and `LINEAGE_DB_PORT` move them. Admin commands run inside the API
container, which already has the database URL:

```sh
docker compose exec api lineage-store token create backport --pipeline backport@<sha> --assert-for-others
```

The image is `Dockerfile` (the API, `lineage-store serve`); migrations run from the stock
dbmate image against `db/migrations`. For working on the code itself, `uv run lineage-store
serve` against the Compose database also works.

## Who can write

Every write names an agent, or the database refuses it.

- **Pipelines** use a token: `docker compose exec api lineage-store token create backport
  --pipeline backport@<sha> --assert-for-others`. The token is printed once. `--assert-for-others` lets an importer
  write claims asserted by the people whose work it carries.
- **People** send a GitHub token. They must be in one of `LINEAGE_GITHUB_TEAMS`
  (`org/team-slug`, comma-separated); their agent is created on first sight, matched by
  GitHub login.
- **QGIS against PostGIS directly**: give each person a database role, and
  `docker compose exec api lineage-store role map <role> --github <login>`.

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

No setup: in QGIS, **Project → Open From → PostgreSQL**, pick the connection, schema
`qgis`, project **Lineage**. It opens with the sections on a time slider (1929 to 1998, a
year a step) over OpenStreetMap. Removals still waiting for review are thick magenta lines,
from the earliest date each could have happened; once a reviewer confirms which section a
removal ended, it disappears, and that section ends instead. Proposed links and county
lines are layers you can switch on. Click a section or removal with **Identify** to see the claims behind
it: what was said, by whom, when it was valid, and the source and place it came from
(the **Claims and sources** table, related to both layers). The county lines follow the slider: each year shows the counties as they
stood then, so they match the old maps. Proposed
ends are dashed; sections whose sources disagree on dates are purple. Allow the project's
macro when QGIS asks: it puts the Temporal Controller in animated mode, which QGIS doesn't
save in projects. Without it, click the animated button in the Temporal Controller.

The county lines come from the Newberry Library's [Atlas of Historical County
Boundaries](https://digital.newberry.org/ahcb/) (free to reuse), simplified to about
100 m. Compose's `counties` service loads them into `reference.historical_county` the
first time (a ~50 MB download); `docker compose run --rm -e FORCE=1 counties` reloads.

The connection is the database Compose runs: host `localhost`, port `47119`, database
`lineage`, user and password `lineage`.

Adding `qgis.sections` by hand also picks up the default style (symbology, and the time
fields), though QGIS leaves the Temporal tab's *Dynamic Temporal Control* box for you to
tick.

`docker compose up` installs the project and the style. Elsewhere, run
`lineage-store qgis install --host <db host as QGIS users reach it> --port <port>`; it
leaves the user and password out unless given, so each person's own credentials apply.
The project is made in QGIS 4.2 and kept in `src/lineage_store/qgis/lineage.qgs`. That file
connects to the Compose database, so you can open it straight from the repo, change it in
QGIS and save it; `install` swaps in the real connection.

The views, if you want them directly: `qgis.sections` (time fields `time_start` and
`time_end`, key `row_id`), `qgis.removals`, `qgis.claims`, `qgis.proposed_links`, `qgis.lines`,
`qgis.rejected_traces`.

## Tests

```sh
docker compose up -d db
uv run pytest
```

Each run makes a fresh database on the server at `LINEAGE_TEST_ADMIN_URL` (default: the
Compose database) and drops it afterwards. All test data is made up (L-0009).
