-- migrate:up

-- Scope and dates for the sections asked for. The rule of `store.claim_scope` and
-- `store.section_dates` is unchanged; it moves into functions that start from the sections
-- asked for, or from every section when given NULL, so one section's scope never costs the
-- whole network's. Each view is its function over every section. A migration of its own,
-- so a database that already has the views gets the functions too. Every statement can
-- run on a database that already has them.

CREATE OR REPLACE FUNCTION store.claim_scope_for(ids uuid[])
RETURNS TABLE (section_id uuid, feature_id uuid)
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE scope (section_id, feature_id, depth) AS (
        SELECT s.feature_id, s.feature_id, 0 FROM store.section s
        WHERE ids IS NULL OR s.feature_id IN (SELECT unnest(ids))
        UNION
        -- a feature merged into this one (confirmed same_as from it to us), or the section
        -- we were split from (confirmed split_into from it to us)
        SELECT sc.section_id, l.from_feature_id, sc.depth + 1
        FROM scope sc
        JOIN store.feature_link l ON l.to_feature_id = sc.feature_id
            AND l.relation IN ('same_as', 'split_into') AND l.status = 'confirmed'
        WHERE sc.depth < 20
    )
    SELECT DISTINCT section_id, feature_id FROM scope
$$;

CREATE OR REPLACE VIEW store.claim_scope AS
SELECT section_id, feature_id FROM store.claim_scope_for(NULL);

CREATE OR REPLACE FUNCTION store.section_dates_for(ids uuid[])
RETURNS TABLE (section_id uuid, existed_from text, time_start date, existed_to text, time_end date,
               date_conflict boolean, end_from date)
LANGUAGE sql STABLE AS $$
    WITH st AS (
        SELECT sc.section_id, c.*
        FROM store.claim_scope_for(ids) sc
        JOIN store.claim c ON c.feature_id = sc.feature_id
        WHERE c.attribute_key = 'status' AND c.status = 'accepted'
    ),
    op AS (
        SELECT st.section_id,
               max(start_earliest) AS lo,
               min(start_latest)   AS hi,
               min(end_earliest)   AS seen
        FROM st WHERE value_text = 'operational' GROUP BY st.section_id
    ),
    dec AS (
        SELECT st.section_id,
               max(start_earliest) AS lo,
               min(start_latest)   AS hi,
               min(start_earliest) AS widest_lo,
               max(start_latest)   AS widest_hi
        FROM st WHERE value_text = 'decommissioned' GROUP BY st.section_id
    ),
    op_pick AS (   -- the claim that sets the narrowest start, for its EDTF text
        SELECT DISTINCT ON (st.section_id) st.section_id, valid_edtf
        FROM st WHERE value_text = 'operational'
        ORDER BY st.section_id, start_earliest DESC NULLS LAST, start_latest ASC NULLS LAST
    ),
    dec_pick AS (  -- the claim that sets the narrowest end
        SELECT DISTINCT ON (st.section_id) st.section_id, valid_edtf
        FROM st WHERE value_text = 'decommissioned'
        ORDER BY st.section_id, start_latest ASC NULLS LAST, start_earliest DESC NULLS LAST
    )
    SELECT s.feature_id AS section_id,
           op_pick.valid_edtf                                                         AS existed_from,
           -- in a conflict, widen to the earliest any claim allows
           CASE WHEN op.lo > op.hi THEN least(op.hi, op.seen)
                ELSE coalesce(op.lo, least(op.hi, op.seen)) END                       AS time_start,
           CASE WHEN dec.lo > dec.hi
                THEN coalesce(dec.widest_lo::text, '..') || '/' || coalesce(dec.widest_hi::text, '..')
                ELSE dec_pick.valid_edtf END                                          AS existed_to,
           CASE WHEN dec.lo > dec.hi THEN dec.widest_hi
                ELSE coalesce(dec.hi, dec.lo) END                                     AS time_end,
           coalesce(op.lo > op.hi, false) OR coalesce(dec.lo > dec.hi, false)         AS date_conflict,
           -- the earliest the end could have come: where a removal starts on the slider
           CASE WHEN dec.lo > dec.hi THEN dec.widest_lo
                ELSE coalesce(dec.lo, dec.hi) END                                     AS end_from
    FROM store.section s
    LEFT JOIN op       ON op.section_id = s.feature_id
    LEFT JOIN dec      ON dec.section_id = s.feature_id
    LEFT JOIN op_pick  ON op_pick.section_id = s.feature_id
    LEFT JOIN dec_pick ON dec_pick.section_id = s.feature_id
    WHERE ids IS NULL OR s.feature_id IN (SELECT unnest(ids))
$$;

CREATE OR REPLACE VIEW store.section_dates AS
SELECT section_id, existed_from, time_start, existed_to, time_end, date_conflict, end_from
FROM store.section_dates_for(NULL);

-- The current route: the newest accepted route claim on the section itself. Claims written
-- in one transaction share `created_at`, so the id settles which is newest.
CREATE OR REPLACE VIEW store.section_route AS
SELECT DISTINCT ON (c.feature_id) c.feature_id AS section_id, c.value_geom AS geom, c.id AS claim_id
FROM store.claim c
WHERE c.attribute_key = 'route' AND c.status = 'accepted'
ORDER BY c.feature_id, c.created_at DESC, c.id DESC;

-- migrate:down

CREATE OR REPLACE VIEW store.section_route AS
SELECT DISTINCT ON (c.feature_id) c.feature_id AS section_id, c.value_geom AS geom, c.id AS claim_id
FROM store.claim c
WHERE c.attribute_key = 'route' AND c.status = 'accepted'
ORDER BY c.feature_id, c.created_at DESC;

CREATE OR REPLACE VIEW store.section_dates AS
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

CREATE OR REPLACE VIEW store.claim_scope AS
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

DROP FUNCTION store.section_dates_for, store.claim_scope_for;
