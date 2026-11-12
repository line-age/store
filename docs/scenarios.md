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

The dates come from the accepted `status` claims: the narrowest date that every one of
them allows, so "by 1937" and "May 1931" give May 1931. `time_start` and `time_end` are the same dates collapsed for the QGIS
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

### A source pins down the year

Someone clicks "Ask AI" on the 1931 line, and `cite` finds a 1958 journal item: "the Hobbs
line was abandoned in October 1958". It writes a candidate claim on the 1931 section, and a
person accepts it. Both `decommissioned` claims are kept:

| On | Says | Cited to |
| --- | --- | --- |
| the removal | started between 1955-06-30 and 1960 | 1960 sheet, `map_omits` |
| the 1931 section | from 1958-10 | 1958 journal, `states` |

They don't disagree; October 1958 is inside 1955 to 1960. The release rule for dates is the
narrowest date that every accepted claim allows:

| id | existed_from | existed_to | time_start | time_end |
| --- | --- | --- | --- | --- |
| 4c1e… | 1931-05 | 1958-10 | 1931-05-01 | 1958-10-31 |

## Case 3: two sources disagree

Case 2's line, but the item `cite` finds says "abandoned in 1962". The 1960 sheet already
leaves the line off, so no single date fits both:

| On | Says | Cited to |
| --- | --- | --- |
| the removal | decommissioned, starting between 1955-06-30 and 1960 | 1960 sheet, `map_omits` |
| the 1931 section | decommissioned from 1962 | 1962 journal, `states` |

A person can still accept the 1962 claim: the journal does say it, and that is all
accepting means. `store` doesn't pick a winner (I-0005).

When the accepted claims can't all be true, `release` widens the date to cover them all and
flags the row:

| id | existed_to | time_end | date_conflict |
| --- | --- | --- | --- |
| 4c1e… | 1955-06-30/1962 | 1962-12-31 | true |

`review` has a "conflicting dates" filter, so someone can go looking for a third source.
Often both are right about different events: a map drops a line when it stops carrying gas,
and "abandoned" in a journal or filing can be the paperwork years later.

Ranking evidence (`states` beats `map_omits`) was the alternative. It hides the
disagreement, and a line missing from a map is right more often than a rule would credit.

## Case 4: only part of the line is removed

The 1931 line runs 100 km. The 1960 sheet still shows the western 60 km but not the
eastern 40 km, and the tracer draws a removal over just that part.

### Tracing

As case 2, except for the link:

| Table | Row |
| --- | --- |
| `FEATURE` | the removal, 40 km |
| `CLAIM` | its `route` (1955 sheet) and `decommissioned` (1960 sheet), as in case 2 |
| `FEATURE_LINK` | `overlaps`, removal to the 1931 section, `proposed`, score 0.98 |

The score is the matcher's confidence: the whole removal lies along the line. How much of
the line it covers (40%) is not stored; it is computed from the two routes, and the link
type already says "part of it" instead of "all of it".

The 1931 section is still one 100 km feature. The QGIS views draw the eastern 40 km
ending, marked as proposed; `release` ignores the proposed link, so the whole line stays
open-ended there.

### Review confirms the cut

In `review` the person sees the 100 km line with the proposed cut where the removal starts
(about km 60), drags it to where the 1960 sheet actually stops drawing the line if need be,
and confirms:

| Table | Row |
| --- | --- |
| `FEATURE` | west piece `7a2f…`, 60 km, and east piece `9d03…`, 40 km |
| `CLAIM` | a `route` for each piece, by the reviewer, cited to the same place on the 1937 sheet as the original route |
| `FEATURE_LINK` | `split_into`, `4c1e` to each piece |
| `FEATURE_LINK` | the `overlaps` link becomes `confirmed`, and a new confirmed `same_as` joins the removal to `9d03` |
| `REVIEW` | confirm, with the cut, by that person |

`4c1e`'s own claims (operational from May 1931, the owner) stay on it. They describe the
whole line, so both pieces inherit them by following `split_into`, the same "follow the
links" rule as case 2. `4c1e` is no longer released; its pieces are:

| id | existed_from | existed_to | time_end |
| --- | --- | --- | --- |
| 7a2f… | 1931-05 | | |
| 9d03… | 1931-05 | 1955-06-30/1960 | 1960-12-31 |

## Case 5: the same pipeline traced twice

Later, someone traces a 1950 state oil and gas map from a different series, and it shows the
Hobbs line too. Nothing in the trace says it is a line already in `store`, so tracing
writes a new section:

| Table | Row |
| --- | --- |
| `FEATURE` | new section `b81c…` |
| `CLAIM` | `route` from the 1950 map, and `operational` valid `../1950`, both cited to it (`map_shows`) |
| `FEATURE_LINK` | `same_as`, `b81c` to `4c1e`, `proposed`, score 0.91 |

Until someone decides, it is a duplicate. That is deliberate (I-0004): a duplicate is
visible, while two different pipelines wrongly fused into one never get noticed.

When a person confirms the link, they also pick which route is released, since the later
map may be drawn better. The link points from the trace set aside to the one kept; the
set-aside route stays in `store` with its citation. `release` shows one line: the dates
combine by the narrowest-date rule ("by 1937" and "by 1950" give "by 1937"), and the
citations from both maps are on it.

## Case 6: the machine traces a railroad

`trace` (the automated tracer, not a person) reads a sheet and draws a line that is really a
railroad, its characteristic mistake. It writes the same rows a person would, except the
claims are candidates:

| Table | Row |
| --- | --- |
| `FEATURE` | new section `e44a…` |
| `CLAIM` | `route`, `candidate`, by `trace` |
| `CLAIM` | `operational` valid `../1937`, `candidate`, by `trace` |

A person sees the railroad symbol on the sheet and clicks "reject trace", which rejects
every candidate claim on the section at once, one `REVIEW` row per claim. Nothing is
deleted: the section stays, holding only rejected claims. `release` skips it (a section
with no accepted `route` isn't a line), `review` stops listing it, and it counts as a false
positive on `trace`'s scorecard and stops `trace` proposing it again. The QGIS views can
show rejected traces in a layer of their own, off by default.

## Case 7: the route is drawn wrong

The tracer cut a corner near Hobbs that the 1937 sheet draws as a dogleg, and a reviewer
redraws that stretch. It is a `correct` verdict, as in the March-to-May example: right
source, right line, wrong value.

| Claim | Value | Status | By |
| --- | --- | --- | --- |
| first route | the hurried line | rejected | the tracer |
| second route | the redrawn line | accepted | the reviewer |

One `REVIEW` row: `correct` on the first, `replaced_by_claim_id` = the second, which is
cited to the same place on the 1937 sheet. Dates, owner and links hang off the section, not
the route, so nothing else changes. The tracer's original stays in the rejected claim.

## Not modelled: re-georeferencing a sheet

Considered and dropped. If a sheet's georeferencing improves, the lines traced on it could
in principle be moved to match, which would mean storing each trace in the scan's pixel
coordinates. It isn't worth it: at these map scales the ink itself is far wider than any
georeferencing error, so the lines are no more accurate after moving them. A badly wrong
georeference is fixed by correcting the routes it spoiled (case 7) or tracing again.

## Case 8: the line changes hands

A 1945 journal item says "El Paso Natural Gas has acquired the Hobbs line from New Mexico
Gas & Oil, effective March 1". That one passage states both ends, so `cite` writes two
claims from it:

| attribute | value | valid | cited to |
| --- | --- | --- | --- |
| owner | New Mexico Gas & Oil Co. | as of 1937 | 1937 sheet |
| owner | New Mexico Gas & Oil Co. | until 1945-03-01 | 1945 journal |
| owner | El Paso Natural Gas | from 1945-03-01 | 1945 journal |

Write what the source states, including an end it states, and don't assume an end just
because a new owner appears: `owner` allows several at once (joint ownership), so a new
name alone could be a half share.

## Case 9: sections grouped into a line

Researchers think in pipelines ("the Hobbs line"); traces arrive as sections. Say the Hobbs
line is `4c1e` (Hobbs to Jal, 1931) and `7b20` (Jal to Kermit, an extension, 1936).

The line is its own `FEATURE`, kind `line` (`ab12…`), and its facts are claims like any
other: `name` = "Hobbs line", cited to the 1945 journal item that calls it that.

No trace says which line a section belongs to, so membership is a judgement (I-0005) and a
link, with the same life cycle as cases 2 to 5:

| `FEATURE_LINK` | from | to | status | by |
| --- | --- | --- | --- | --- |
| `part_of` | `4c1e` | `ab12` | confirmed | a reviewer |
| `part_of` | `7b20` | `ab12` | proposed | the matcher, score 0.85 |

A link can be cited, for example to an item saying the line "was extended to Kermit in
1936". `FEATURE_LINK` holds every kind of link in one table because they all share that
life cycle; each kind fixes which feature kinds its ends may be (see model.md).

In the release, the map stays on sections. A `lines` layer with the sections glued into one
geometry would break the time slider, drawing the 1936 extension in 1931. So `sections`
gains a `line_id` column, taken only from confirmed `part_of` links, and `lines` is a
table with no geometry:

| id | label |
| --- | --- |
| ab12… | Hobbs line |

In QGIS you colour or label sections by line, and the extension still appears in 1936.
`label` is the display-only field every feature has, filled from the line's accepted `name`
claim; a renamed line keeps all its names, dated and cited, in `claims`. Until `7b20`'s
link is confirmed, `7b20` is released with no line.

## Not yet worked

Left until real data pushes on them, most likely the research partner's spreadsheets:

- A line relaid with bigger pipe on the same route (a new section; how it links to the old
  one, and which section the later maps' citations belong to).
- A line that disappears from one sheet and comes back on the next.
- A line extended from its end by a later trace.
