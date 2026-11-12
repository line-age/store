# Scenarios

Worked cases for the model in [model.md](model.md), from the simplest up. Each one says what
gets stored, how it changes through tracing, citing and review, and how it comes out in a
release. Agreed one at a time; a case here is settled unless it says otherwise.

## Reviews: what the verdicts mean

A machine (`cite` or `trace`) proposes a claim; a person answers it. A claim is never edited
in place.

- **accept**: the source says it, and it is about this section. The claim becomes
  `accepted`.
- **reject**: the source doesn't say it, it is about another line, or the source doesn't
  exist. The claim becomes `rejected` and stays in the database, as a record of the
  machine's mistake.
- **correct**: the right source and the right line, but the wrong value. The claim becomes
  `rejected`, the reviewer writes a new claim with the right value cited to the same source,
  and the review points to it (`REVIEW.replaced_by_claim_id`).

Example: `cite` reads a 1931 page saying "completed in May 1931" and proposes "operational
from 1931-03". The reviewer corrects it:

| Claim | Value | Status | By |
| --- | --- | --- | --- |
| first | operational from 1931-03 | rejected | `cite` |
| second | operational from 1931-05 | accepted | the reviewer |

One review row: verdict `correct` on the first claim, `replaced_by_claim_id` = the second.

## Case 1: one section, built once, never shut down

A gas line built in May 1931 and still running at the end of the record.

### Tracing

A person traces it from a sheet dated "as of 1937". Nobody knows the build year yet. One
API call writes:

| Table | Row |
| --- | --- |
| `SOURCE` | the sheet: `map_sheet`, `as_of_edtf` = `1937` (usually already there) |
| `AGENT` | the tracer, a person (already there) |
| `FEATURE` | a new section, network `gas` |
| `CLAIM` | `route` = the traced line, valid `../1937` |
| `CLAIM` | `status` = `operational`, valid `../1937` (existed by 1937; I-0003) |
| `CITATION` | the sheet, and where on it |
| `CLAIM_CITATION` | both claims to that citation, role `map_shows` |

A person made these claims, so they are `accepted` at once. Had `trace` drawn the line,
they would be `candidate` until reviewed. Every insert also writes a history row naming the
agent. There is no build date, owner or diameter yet, and nothing is filled in (I-0001).

### Citing

Two ways to find more, and they end in the same place.

**Ask AI.** A person looking at the section in `review` clicks "Ask AI". `review` records a
`CITATION_REQUEST` (this section, anything we don't know, priority `interactive`) and has
`cite` process it at once. `cite` gets everything known (the route, "existed by 1937", the
sheet) and searches for the rest.

**Background.** A bulk job records requests for many sections at once, priority
`background`, and `cite` works through them.

Either way, `cite` finds a June 1931 journal page saying the line was "completed in May
1931" and writes, straight away:

| Table | Row |
| --- | --- |
| `SOURCE` | that issue of the journal (once; reused by anything else cited from it) |
| `CITATION` | page 40, with the quoted sentence |
| `CLAIM` | `status` = `operational`, valid `1931-05/..`, `candidate`, by `cite`, `request_id` = the request |
| `CLAIM_CITATION` | the claim to page 40, role `states` |

A person accepts it. The section now has two `status` claims, both kept:

| Says | From |
| --- | --- |
| operational, existed by 1937 | the sheet |
| operational from May 1931 | the journal page |

They don't disagree: "running by 1937" and "started May 1931" are both true.

### Reading a fact off the map

A person sees "New Mexico Gas & Oil Co." printed along the line on the 1937 sheet and adds
it directly: an `owner` claim, valid `../1937` (owned by them as of the sheet date, the same
map-date rule), `accepted`, by that person, cited to the sheet and where on it the label is.
No AI and no review step, because a person made it.

### The release

`release` writes three tables, joined by GeoPackage related tables so QGIS shows them
together when you click a line. Only `accepted` claims go in by default (whether a research
build carries candidates is still open).

Which table an attribute goes in depends on whether it can change during one section's
life. A section is built and put in use at one time (L-0011), so:

- **Fixed for the section's life** (route, diameter, build date): a column on `sections`.
  Pipe relaid at a new diameter is a new section.
- **Status** is the section's lifespan, so it is the date columns, not a column of its own.
- **Can change while the pipe stays put** (owner, operator): rows in `claims` only. A single
  column would be wrong for part of the line's life.

Every value, column or not, also has its row in `claims`, because that is where its
citation hangs.

`sections`

| id | existed_from | existed_to | time_start | time_end | diameter |
| --- | --- | --- | --- | --- | --- |
| 4c1e… | 1931-05 | | 1931-05-01 | | |

The dates come from the accepted `status` claims: the earliest start wins, so May 1931
beats "by 1937". `time_start` and `time_end` are the same dates collapsed for the QGIS
Temporal Controller (Legend 0011). `diameter` is empty because nobody has found it.

`claims`

| section | attribute | value | valid | citation |
| --- | --- | --- | --- | --- |
| 4c1e… | route | the traced line | ../1937 | 1937 sheet |
| 4c1e… | status | operational | ../1937 | 1937 sheet |
| 4c1e… | status | operational | 1931-05/.. | journal, p. 40 |
| 4c1e… | owner | New Mexico Gas & Oil Co. | ../1937 | 1937 sheet |

`citations`

| id | source | where | quote |
| --- | --- | --- | --- |
| … | the 1937 sheet | the line near Hobbs; the owner label | |
| … | the journal, June 1931 | p. 40 | "completed in May 1931" |

In QGIS the line appears in 1931 and stays, and you can colour or filter by diameter and
dates straight off the layer. Clicking it shows its claims, and each claim's citation. You
can't colour the map by owner; if that turns out to matter, it is a release format (one row
per section per owner period, or per year), and the model doesn't change.

## Case 2: the same line, shut down in 1958

Case 1's line, except it was abandoned in 1958. Nobody has a document saying so yet.

### Tracing the shutdown

All there is at first is two sheets: the "as of June 30, 1955" sheet shows the line, and
the 1960 sheet doesn't. The tracer draws the vanished line on the 1955 sheet as a removal
(the research partner's `sub` files). One API call writes:

| Table | Row |
| --- | --- |
| `FEATURE` | a new section, the removal, separate from the 1931 line |
| `CLAIM` | `route` = the line as drawn, valid `../1955-06-30`, cited to the 1955 sheet (`map_shows`) |
| `CLAIM` | `status` = `decommissioned`, starting between 1955-06-30 and 1960, cited to the 1960 sheet (`map_omits`) |
| `FEATURE_LINK` | `same_as`, removal to the 1931 section, `proposed`, with its score, asserted by the matching script |

The two sheets are facts; "this removal is that line" is a judgement, so the system only
proposes it (I-0004). The 1931 section's own claims don't change. If the match is wrong,
rejecting the link leaves nothing to undo.

### Review decides the link

In `review`, the "proposed removals" filter lists the pair: both lines over the 1955 and
1960 sheets, with the score.

**Confirm.** The link goes from `proposed` to `confirmed`, and a `REVIEW` row records who
confirmed it. Nothing is copied onto the 1931 section: the `decommissioned` claim stays on
the removal, where the sheets put it, and the confirmed link is what says the two are one
line. Anything that reads dates follows confirmed `same_as` links.

`release` treats a confirmed `same_as` pair as one row, with the route from the original
trace and dates from both features' status claims:

| id | existed_from | existed_to | time_start | time_end |
| --- | --- | --- | --- | --- |
| 4c1e… | 1931-05 | 1955-06-30/1960 | 1931-05-01 | 1960-12-31 |

**Reject.** The link becomes `rejected`, the 1931 line stays open-ended, and the removal
stays on the "unmatched removals" list until someone finds the line it belongs to.

**Undecided.** `release` ignores proposed links, as it leaves out candidate claims. The
QGIS views draw the proposed end, marked as proposed.

Copying a `decommissioned` claim onto the section at confirmation was the alternative. It
is simpler to read, but it is a duplicate that has to be unwound if the confirmation is
ever reversed.
