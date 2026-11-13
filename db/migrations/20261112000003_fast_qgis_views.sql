-- migrate:up

-- Built whole, then filtered. QGIS filters on the computed geometry (geom && <map extent>);
-- the planner can't estimate that, guesses one row, and picks a plan that recomputes the
-- dates per row: 12 s instead of 0.2 s on the natural gas import. MATERIALIZED stops the
-- filter being pushed inside.
CREATE OR REPLACE VIEW qgis.sections AS
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
CREATE OR REPLACE VIEW qgis.removals AS
WITH all_rows AS MATERIALIZED (
    SELECT rm.feature_id AS removal_id,
           ST_Multi(r.geom)::geometry(MultiLineString, 4326) AS geom,
           d.existed_to, d.time_end,
           CASE WHEN EXISTS (SELECT 1 FROM store.feature_link l WHERE l.from_feature_id = rm.feature_id
                             AND l.relation IN ('same_as', 'overlaps') AND l.status = 'confirmed') THEN 'confirmed'
                WHEN EXISTS (SELECT 1 FROM store.feature_link l WHERE l.from_feature_id = rm.feature_id
                             AND l.relation IN ('same_as', 'overlaps') AND l.status = 'proposed') THEN 'proposed'
                ELSE 'unmatched' END AS match_status
    FROM store.removal rm
    JOIN store.section_route r ON r.section_id = rm.feature_id
    JOIN store.section_dates d ON d.section_id = rm.feature_id
)
SELECT * FROM all_rows;

-- migrate:down

CREATE OR REPLACE VIEW qgis.sections AS
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
WHERE NOT ST_IsEmpty(ST_CollectionExtract(r.geom, 2));

CREATE OR REPLACE VIEW qgis.removals AS
SELECT rm.feature_id AS removal_id,
       ST_Multi(r.geom)::geometry(MultiLineString, 4326) AS geom,
       d.existed_to, d.time_end,
       CASE WHEN EXISTS (SELECT 1 FROM store.feature_link l WHERE l.from_feature_id = rm.feature_id
                         AND l.relation IN ('same_as', 'overlaps') AND l.status = 'confirmed') THEN 'confirmed'
            WHEN EXISTS (SELECT 1 FROM store.feature_link l WHERE l.from_feature_id = rm.feature_id
                         AND l.relation IN ('same_as', 'overlaps') AND l.status = 'proposed') THEN 'proposed'
            ELSE 'unmatched' END AS match_status
FROM store.removal rm
JOIN store.section_route r ON r.section_id = rm.feature_id
JOIN store.section_dates d ON d.section_id = rm.feature_id;
