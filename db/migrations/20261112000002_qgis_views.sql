-- migrate:up

-- Read views for QGIS (plan.md, "QGIS views"), following docs/scenarios.md:
--
-- - Only accepted claims count. Rejected and candidate claims never move a date.
-- - Reading follows confirmed links: a feature merged into another by a confirmed
--   `same_as` lends it its status claims (case 2), and a piece made by a confirmed
--   `split_into` inherits its parent's (case 4).
-- - Dates are the narrowest every accepted claim allows; when they can't all be true the
--   date widens to cover them and `date_conflict` is set (cases 2 and 3).
-- - Proposed links are drawn, marked `end_status = 'proposed'` (release ignores them).
--
-- A removal is a section with an accepted `decommissioned` claim cited `map_omits`: what a
-- tracer draws for a line that one sheet shows and the next doesn't. Removals are not
-- drawn as sections; they lend their end to the section they are linked to.

CREATE SCHEMA qgis;

-- Removals ---------------------------------------------------------------------------------

CREATE VIEW store.removal AS
SELECT DISTINCT c.feature_id
FROM store.claim c
JOIN store.claim_citation cc ON cc.claim_id = c.id AND cc.role = 'map_omits'
WHERE c.attribute_key = 'status' AND c.value_text = 'decommissioned' AND c.status = 'accepted';

-- Which features' claims describe a section ----------------------------------------------

CREATE VIEW store.claim_scope AS
WITH RECURSIVE scope (section_id, feature_id, depth) AS (
    SELECT s.feature_id, s.feature_id, 0 FROM store.section s
    UNION
    -- a feature merged into this one (confirmed same_as from it to us), or the section we
    -- were split from (confirmed split_into from it to us)
    SELECT sc.section_id, l.from_feature_id, sc.depth + 1
    FROM scope sc
    JOIN store.feature_link l ON l.to_feature_id = sc.feature_id
        AND l.relation IN ('same_as', 'split_into') AND l.status = 'confirmed'
    WHERE sc.depth < 20
)
SELECT DISTINCT section_id, feature_id FROM scope;

-- Dates ------------------------------------------------------------------------------------

CREATE VIEW store.section_dates AS
WITH st AS (
    SELECT sc.section_id, c.*
    FROM store.claim_scope sc
    JOIN store.claim c ON c.feature_id = sc.feature_id
    WHERE c.attribute_key = 'status' AND c.status = 'accepted'
),
op AS (
    SELECT section_id,
           max(start_earliest) AS lo,
           min(start_latest)   AS hi,
           min(end_earliest)   AS seen
    FROM st WHERE value_text = 'operational' GROUP BY section_id
),
dec AS (
    SELECT section_id,
           max(start_earliest) AS lo,
           min(start_latest)   AS hi,
           min(start_earliest) AS widest_lo,
           max(start_latest)   AS widest_hi
    FROM st WHERE value_text = 'decommissioned' GROUP BY section_id
),
op_pick AS (   -- the claim that sets the narrowest start, for its EDTF text
    SELECT DISTINCT ON (section_id) section_id, valid_edtf
    FROM st WHERE value_text = 'operational'
    ORDER BY section_id, start_earliest DESC NULLS LAST, start_latest ASC NULLS LAST
),
dec_pick AS (  -- the claim that sets the narrowest end
    SELECT DISTINCT ON (section_id) section_id, valid_edtf
    FROM st WHERE value_text = 'decommissioned'
    ORDER BY section_id, start_latest ASC NULLS LAST, start_earliest DESC NULLS LAST
)
SELECT s.feature_id AS section_id,
       op_pick.valid_edtf                                                           AS existed_from,
       -- in a conflict, widen to the earliest any claim allows
       CASE WHEN op.lo > op.hi THEN least(op.hi, op.seen)
            ELSE coalesce(op.lo, least(op.hi, op.seen)) END                         AS time_start,
       CASE WHEN dec.lo > dec.hi
            THEN coalesce(dec.widest_lo::text, '..') || '/' || coalesce(dec.widest_hi::text, '..')
            ELSE dec_pick.valid_edtf END                                            AS existed_to,
       CASE WHEN dec.lo > dec.hi THEN dec.widest_hi
            ELSE coalesce(dec.hi, dec.lo) END                                       AS time_end,
       coalesce(op.lo > op.hi, false) OR coalesce(dec.lo > dec.hi, false)           AS date_conflict,
       -- the earliest the end could have come: where a removal starts on the slider
       CASE WHEN dec.lo > dec.hi THEN dec.widest_lo
            ELSE coalesce(dec.lo, dec.hi) END                                       AS end_from
FROM store.section s
LEFT JOIN op       ON op.section_id = s.feature_id
LEFT JOIN dec      ON dec.section_id = s.feature_id
LEFT JOIN op_pick  ON op_pick.section_id = s.feature_id
LEFT JOIN dec_pick ON dec_pick.section_id = s.feature_id;

-- The current route: the newest accepted route claim on the section itself.
CREATE VIEW store.section_route AS
SELECT DISTINCT ON (c.feature_id) c.feature_id AS section_id, c.value_geom AS geom, c.id AS claim_id
FROM store.claim c
WHERE c.attribute_key = 'route' AND c.status = 'accepted'
ORDER BY c.feature_id, c.created_at DESC;

-- Sections that are drawn: accepted route, not a removal, not merged into another, not
-- replaced by its split pieces.
CREATE VIEW store.drawn_section AS
SELECT r.section_id, r.geom
FROM store.section_route r
WHERE r.section_id NOT IN (SELECT feature_id FROM store.removal)
  AND NOT EXISTS (SELECT 1 FROM store.feature_link l
                  WHERE l.from_feature_id = r.section_id AND l.status = 'confirmed'
                    AND l.relation IN ('same_as', 'split_into'));

-- Proposed ends: a removal linked to a drawn section by a proposed `same_as` (the whole
-- section) or a proposed `overlaps` (the part within 1 km of the removal).
CREATE VIEW store.proposed_end AS
SELECT l.id AS link_id, l.relation, l.score, l.to_feature_id AS section_id,
       l.from_feature_id AS removal_id, d.existed_to, d.time_end, rr.geom AS removal_geom
FROM store.feature_link l
JOIN store.removal rm ON rm.feature_id = l.from_feature_id
JOIN store.section_dates d ON d.section_id = l.from_feature_id
JOIN store.section_route rr ON rr.section_id = l.from_feature_id
WHERE l.status = 'proposed' AND l.relation IN ('same_as', 'overlaps');

CREATE FUNCTION store.near(a geometry, b geometry) RETURNS geometry
LANGUAGE sql IMMUTABLE AS $$
    -- the part of a within 1 km of b, as the stitch script measured it (EPSG:5070, metres)
    SELECT ST_Transform(ST_Intersection(ST_Transform(a, 5070), ST_Buffer(ST_Transform(b, 5070), 1000)), 4326)
$$;
CREATE FUNCTION store.away(a geometry, b geometry) RETURNS geometry
LANGUAGE sql IMMUTABLE AS $$
    SELECT ST_Transform(ST_Difference(ST_Transform(a, 5070), ST_Buffer(ST_Transform(b, 5070), 1000)), 4326)
$$;

-- QGIS -------------------------------------------------------------------------------------

-- One row per drawn section, or per piece where a proposed `overlaps` link cuts one.
-- `row_id` is the section id, or the link id for a proposed piece.
-- Built whole, then filtered. QGIS filters on the computed geometry (geom && <map extent>);
-- the planner can't estimate that, guesses one row, and picks a plan that recomputes the
-- dates per row: 12 s instead of 0.2 s on the natural gas import. MATERIALIZED stops the
-- filter being pushed inside.
CREATE VIEW qgis.sections AS
WITH all_rows AS MATERIALIZED (
    WITH base AS (
        SELECT ds.section_id, ds.geom, f.label, f.network, d.existed_from, d.existed_to,
               d.time_start, d.time_end, d.date_conflict
        FROM store.drawn_section ds
        JOIN store.feature f ON f.id = ds.section_id
        JOIN store.section_dates d ON d.section_id = ds.section_id
    ),
    whole_end AS (   -- proposed same_as: the whole section, earliest proposed end
        SELECT DISTINCT ON (section_id) section_id, existed_to, time_end
        FROM store.proposed_end WHERE relation = 'same_as'
        ORDER BY section_id, time_end
    ),
    cuts AS (
        SELECT section_id, ST_Union(removal_geom) AS removals
        FROM store.proposed_end WHERE relation = 'overlaps' GROUP BY section_id
    ),
    rows AS (
        -- the section, minus any proposed cut pieces
        SELECT b.section_id AS row_id, b.section_id,
               CASE WHEN c.removals IS NULL THEN b.geom ELSE store.away(b.geom, c.removals) END AS geom,
               b.label, b.network, b.existed_from,
               coalesce(b.existed_to, w.existed_to) AS existed_to,
               b.time_start,
               coalesce(b.time_end, w.time_end) AS time_end,
               CASE WHEN b.time_end IS NOT NULL THEN 'confirmed'
                    WHEN w.time_end IS NOT NULL THEN 'proposed' END AS end_status,
               b.date_conflict
        FROM base b
        LEFT JOIN whole_end w ON w.section_id = b.section_id
        LEFT JOIN cuts c ON c.section_id = b.section_id
        UNION ALL
        -- each proposed cut piece, ending as its removal says
        SELECT p.link_id, b.section_id, store.near(b.geom, p.removal_geom),
               b.label, b.network, b.existed_from,
               coalesce(b.existed_to, p.existed_to),
               b.time_start,
               coalesce(b.time_end, p.time_end),
               CASE WHEN b.time_end IS NOT NULL THEN 'confirmed' ELSE 'proposed' END,
               b.date_conflict
        FROM base b
        JOIN store.proposed_end p ON p.section_id = b.section_id AND p.relation = 'overlaps'
    ),
    scoped AS (
        SELECT sc.section_id, c.attribute_key, c.value_num, c.value_unit, l.to_feature_id AS line_id, l.created_at
        FROM store.claim_scope sc
        LEFT JOIN store.claim c ON c.feature_id = sc.feature_id AND c.attribute_key = 'diameter' AND c.status = 'accepted'
        LEFT JOIN store.feature_link l ON l.from_feature_id = sc.feature_id AND l.relation = 'part_of'
            AND l.status = 'confirmed'
    ),
    line_of AS (
        SELECT DISTINCT ON (section_id) section_id, line_id FROM scoped WHERE line_id IS NOT NULL
        ORDER BY section_id, created_at DESC
    ),
    diameter_of AS (
        SELECT section_id, string_agg(DISTINCT value_num::text || coalesce(' ' || value_unit, ''), '; ') AS diameter
        FROM scoped WHERE value_num IS NOT NULL GROUP BY section_id
    ),
    candidates AS (
        SELECT DISTINCT feature_id FROM store.claim WHERE status = 'candidate'
    )
    SELECT r.row_id, r.section_id,
           ST_Multi(ST_CollectionExtract(r.geom, 2))::geometry(MultiLineString, 4326) AS geom,
           r.label, r.network, r.existed_from, r.existed_to, r.time_start, r.time_end,
           r.end_status, r.date_conflict, lo.line_id, d.diameter,
           (ca.feature_id IS NOT NULL) AS has_candidates
    FROM rows r
    LEFT JOIN line_of lo ON lo.section_id = r.section_id
    LEFT JOIN diameter_of d ON d.section_id = r.section_id
    LEFT JOIN candidates ca ON ca.feature_id = r.section_id
    WHERE NOT ST_IsEmpty(ST_CollectionExtract(r.geom, 2))
)
SELECT * FROM all_rows;

-- The same fence, for the same reason.
-- Removals still waiting for a decision: a map shows a line gone and nobody has yet confirmed
-- which section it ended. Once a link from it is confirmed, its end date is the section's
-- and it drops out of here; rejecting its decommissioned claim drops it too. On the time
-- slider it appears from the earliest date it could have happened and stays: a removal
-- never stops having happened.
CREATE VIEW qgis.removals AS
WITH all_rows AS MATERIALIZED (
    SELECT rm.feature_id AS removal_id,
           ST_Multi(r.geom)::geometry(MultiLineString, 4326) AS geom,
           d.existed_to AS removed_between,   -- EDTF window, e.g. 1929-08/1931
           d.end_from   AS time_start,
           NULL::date   AS time_end,
           CASE WHEN EXISTS (SELECT 1 FROM store.feature_link l WHERE l.from_feature_id = rm.feature_id
                             AND l.relation IN ('same_as', 'overlaps') AND l.status = 'proposed') THEN 'proposed'
                ELSE 'unmatched' END AS match_status
    FROM store.removal rm
    JOIN store.section_route r ON r.section_id = rm.feature_id
    JOIN store.section_dates d ON d.section_id = rm.feature_id
    WHERE NOT EXISTS (SELECT 1 FROM store.feature_link l WHERE l.from_feature_id = rm.feature_id
                      AND l.relation IN ('same_as', 'overlaps') AND l.status = 'confirmed')
)
SELECT * FROM all_rows;

CREATE VIEW qgis.proposed_links AS
SELECT l.id AS link_id, l.relation, l.score, l.from_feature_id, l.to_feature_id,
       ST_MakeLine(ST_PointOnSurface(a.geom), ST_PointOnSurface(b.geom))::geometry(LineString, 4326) AS geom
FROM store.feature_link l
JOIN store.section_route a ON a.section_id = l.from_feature_id
JOIN store.section_route b ON b.section_id = l.to_feature_id
WHERE l.status = 'proposed';

CREATE VIEW qgis.lines AS
SELECT f.id AS line_id, f.label, f.network
FROM store.feature f WHERE f.kind = 'line';

-- Rejected traces (scenarios, case 6), for anyone checking trace's mistakes.
CREATE VIEW qgis.rejected_traces AS
SELECT DISTINCT ON (c.feature_id) c.feature_id AS section_id, c.id AS claim_id,
       ST_Multi(c.value_geom)::geometry(MultiLineString, 4326) AS geom, a.name AS asserted_by
FROM store.claim c JOIN store.agent a ON a.id = c.asserted_by
WHERE c.attribute_key = 'route' AND c.status = 'rejected'
  AND NOT EXISTS (SELECT 1 FROM store.section_route r WHERE r.section_id = c.feature_id)
ORDER BY c.feature_id, c.created_at DESC;

-- migrate:down

DROP SCHEMA qgis CASCADE;
DROP VIEW store.drawn_section, store.proposed_end, store.section_route, store.section_dates,
          store.claim_scope, store.removal CASCADE;
DROP FUNCTION store.near, store.away;
