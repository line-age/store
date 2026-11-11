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
