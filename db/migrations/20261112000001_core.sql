-- migrate:up

-- The store model (docs/model.md), v0 scope (docs/plan.md). Tables live in `store`,
-- history in `audit`, read views for QGIS in `qgis` (a later migration).

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA store;
CREATE SCHEMA audit;

-- Agents ---------------------------------------------------------------------------------

CREATE TABLE store.agent (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind         text NOT NULL CHECK (kind IN ('person', 'pipeline')),
    name         text NOT NULL,
    github_login text UNIQUE,
    email        text,
    pipeline     text,                 -- pipelines: name and version, e.g. "stitch@a318e90"
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- A database role that writes directly (QGIS) resolves to an agent through this table.
CREATE TABLE store.agent_role (
    role     name PRIMARY KEY,
    agent_id uuid NOT NULL REFERENCES store.agent
);

-- API tokens for pipelines. Only the hash is kept.
CREATE TABLE store.agent_token (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id     uuid NOT NULL REFERENCES store.agent,
    token_sha256 text NOT NULL UNIQUE,
    -- May write claims asserted by another agent (backport importing a tracer's work).
    may_assert_for_others boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now(),
    revoked_at   timestamptz
);

-- The agent every write is attributed to: the API sets lineage.agent_id per transaction;
-- a direct connection resolves through its role. NULL means nobody, and writes refuse.
CREATE FUNCTION store.current_agent() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT coalesce(
        nullif(current_setting('lineage.agent_id', true), '')::uuid,
        (SELECT agent_id FROM store.agent_role WHERE role = session_user)
    )
$$;

-- Attribute registry ---------------------------------------------------------------------

CREATE TABLE store.attribute (
    key           text PRIMARY KEY,
    value_type    text NOT NULL CHECK (value_type IN ('text', 'quantity', 'enum', 'geometry', 'feature')),
    cardinality   text NOT NULL CHECK (cardinality IN ('one', 'many')),
    feature_kinds text[] NOT NULL,
    networks      text[],              -- NULL: any network
    enum_values   text[]               -- enum attributes only
);

-- Features -------------------------------------------------------------------------------

CREATE TABLE store.feature (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind       text NOT NULL CHECK (kind IN ('line', 'section', 'station', 'node', 'edge')),
    network    text NOT NULL CHECK (network IN ('gas', 'oil', 'refined', 'electric', 'road', 'water')),
    label      text,                   -- display only, never evidence
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE store.line    (feature_id uuid PRIMARY KEY REFERENCES store.feature);
CREATE TABLE store.section (feature_id uuid PRIMARY KEY REFERENCES store.feature);

-- Topology. In the first migration so the shape is settled, but nothing writes here in v0
-- (plan: "Later").
CREATE TABLE store.node (
    feature_id uuid PRIMARY KEY REFERENCES store.feature,
    kind       text NOT NULL CHECK (kind IN ('junction', 'end_node')),
    geom       geometry(Point, 4326) NOT NULL
);
CREATE TABLE store.station (
    feature_id uuid PRIMARY KEY REFERENCES store.feature,
    node_id    uuid REFERENCES store.node
);
CREATE TABLE store.edge (
    feature_id   uuid PRIMARY KEY REFERENCES store.feature,
    from_node_id uuid NOT NULL REFERENCES store.node,
    to_node_id   uuid NOT NULL REFERENCES store.node,
    geom         geometry(LineString, 4326)
);
CREATE TABLE store.edge_section (
    edge_id    uuid NOT NULL REFERENCES store.edge,
    section_id uuid NOT NULL REFERENCES store.section,
    PRIMARY KEY (edge_id, section_id)
);

-- Lines and sections need no columns of their own, so their subtype row follows the
-- feature automatically.
CREATE FUNCTION store.feature_subtype() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.kind = 'line' THEN
        INSERT INTO store.line VALUES (NEW.id);
    ELSIF NEW.kind = 'section' THEN
        INSERT INTO store.section VALUES (NEW.id);
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER feature_subtype AFTER INSERT ON store.feature
    FOR EACH ROW EXECUTE FUNCTION store.feature_subtype();

CREATE FUNCTION store.feature_kind_fixed() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.kind <> OLD.kind THEN
        RAISE EXCEPTION 'a feature''s kind never changes (% -> %)', OLD.kind, NEW.kind;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER feature_kind_fixed BEFORE UPDATE ON store.feature
    FOR EACH ROW EXECUTE FUNCTION store.feature_kind_fixed();

-- Evidence -------------------------------------------------------------------------------

CREATE TABLE store.source (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id      uuid REFERENCES store.source,
    kind           text NOT NULL CHECK (kind IN ('periodical', 'issue', 'map_sheet', 'filing', 'dataset', 'web')),
    title          text NOT NULL,
    published_edtf text,               -- the imprint
    as_of_edtf     text,               -- the data cutoff, when the source states one (I-0003)
    identifier     text,               -- IA item, IIIF manifest, scan file names
    annotation_uri text,               -- map sheets: georef annotation
    lane           text,               -- license / retrieval path (I-0006)
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE store.citation (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id   uuid NOT NULL REFERENCES store.source,
    locator     text,                  -- vol, no, page / where on the sheet
    quote       text,
    url         text,
    archive_url text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE store.citation_request (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    feature_id     uuid NOT NULL REFERENCES store.feature,
    attribute_keys text[] NOT NULL DEFAULT '{}',   -- empty: anything
    requested_by   uuid NOT NULL REFERENCES store.agent,
    priority       text NOT NULL CHECK (priority IN ('interactive', 'background')),
    status         text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    started_at     timestamptz,
    finished_at    timestamptz,
    run            jsonb,              -- model, version, cost
    error          text
);
CREATE INDEX citation_request_queue ON store.citation_request (priority, created_at) WHERE status = 'queued';

CREATE TABLE store.claim (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    feature_id       uuid NOT NULL REFERENCES store.feature,
    attribute_key    text NOT NULL REFERENCES store.attribute,
    value_text       text,
    value_num        numeric,
    value_unit       text,             -- as written: in, kV
    value_geom       geometry(Geometry, 4326),
    value_feature_id uuid REFERENCES store.feature,
    valid_edtf       text,             -- as the source said it; display only
    start_earliest   date,
    start_latest     date,
    end_earliest     date,
    end_latest       date,
    status           text NOT NULL CHECK (status IN ('candidate', 'accepted', 'rejected')),
    asserted_by      uuid NOT NULL REFERENCES store.agent,
    request_id       uuid REFERENCES store.citation_request,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (start_earliest IS NULL OR start_latest IS NULL OR start_earliest <= start_latest),
    CHECK (end_earliest IS NULL OR end_latest IS NULL OR end_earliest <= end_latest)
);
CREATE INDEX claim_feature ON store.claim (feature_id, attribute_key);
CREATE INDEX claim_geom ON store.claim USING gist (value_geom);

CREATE TABLE store.claim_citation (
    claim_id    uuid NOT NULL REFERENCES store.claim,
    citation_id uuid NOT NULL REFERENCES store.citation,
    role        text NOT NULL CHECK (role IN ('states', 'map_shows', 'map_omits', 'confirms')),
    confidence  numeric,
    PRIMARY KEY (claim_id, citation_id, role)
);
CREATE INDEX claim_citation_citation ON store.claim_citation (citation_id);

-- A claim's value must fit its attribute.
CREATE FUNCTION store.claim_fits_attribute() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    a store.attribute;
    f store.feature;
BEGIN
    SELECT * INTO a FROM store.attribute WHERE key = NEW.attribute_key;
    SELECT * INTO f FROM store.feature WHERE id = NEW.feature_id;
    IF NOT f.kind = ANY (a.feature_kinds) THEN
        RAISE EXCEPTION '% does not apply to a %', a.key, f.kind;
    END IF;
    IF a.networks IS NOT NULL AND NOT f.network = ANY (a.networks) THEN
        RAISE EXCEPTION '% does not apply to the % network', a.key, f.network;
    END IF;
    IF (a.value_type IN ('text', 'enum') AND NEW.value_text IS NULL)
       OR (a.value_type = 'quantity' AND NEW.value_num IS NULL)
       OR (a.value_type = 'geometry' AND NEW.value_geom IS NULL)
       OR (a.value_type = 'feature' AND NEW.value_feature_id IS NULL) THEN
        RAISE EXCEPTION '% needs a % value', a.key, a.value_type;
    END IF;
    IF a.value_type = 'enum' AND NOT NEW.value_text = ANY (a.enum_values) THEN
        RAISE EXCEPTION '% is not a value of % (%)', NEW.value_text, a.key, array_to_string(a.enum_values, ', ');
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER claim_fits_attribute BEFORE INSERT OR UPDATE OF attribute_key, feature_id ON store.claim
    FOR EACH ROW EXECUTE FUNCTION store.claim_fits_attribute();

-- Claims are never edited in place: only the status moves, and the feature a claim is
-- about can be reassigned (an audited judgement; model.md, Open).
CREATE FUNCTION store.claim_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'claims are never deleted; reject them';
    END IF;
    IF (NEW.attribute_key, NEW.value_text, NEW.value_num, NEW.value_unit, NEW.value_feature_id,
        NEW.valid_edtf, NEW.start_earliest, NEW.start_latest, NEW.end_earliest, NEW.end_latest,
        NEW.asserted_by, NEW.request_id)
       IS DISTINCT FROM
       (OLD.attribute_key, OLD.value_text, OLD.value_num, OLD.value_unit, OLD.value_feature_id,
        OLD.valid_edtf, OLD.start_earliest, OLD.start_latest, OLD.end_earliest, OLD.end_latest,
        OLD.asserted_by, OLD.request_id)
       OR NOT ST_Equals(coalesce(NEW.value_geom, 'POINT EMPTY'), coalesce(OLD.value_geom, 'POINT EMPTY'))
    THEN
        RAISE EXCEPTION 'claims are never edited in place; correct them (docs/scenarios.md)';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER claim_immutable BEFORE UPDATE OR DELETE ON store.claim
    FOR EACH ROW EXECUTE FUNCTION store.claim_immutable();

-- No claim without a citation, not even briefly (I-0002): checked at commit, so a bundle
-- can insert the claim and then its citations in one transaction.
CREATE FUNCTION store.claim_has_citation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM store.claim WHERE id = NEW.id)
       AND NOT EXISTS (SELECT 1 FROM store.claim_citation WHERE claim_id = NEW.id) THEN
        RAISE EXCEPTION 'claim % has no citation (I-0002)', NEW.id;
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER claim_has_citation AFTER INSERT ON store.claim
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION store.claim_has_citation();

CREATE FUNCTION store.claim_citation_kept() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'a claim''s citations are never removed';
END $$;
CREATE TRIGGER claim_citation_kept BEFORE DELETE ON store.claim_citation
    FOR EACH ROW EXECUTE FUNCTION store.claim_citation_kept();

-- Identity -------------------------------------------------------------------------------

CREATE TABLE store.feature_link (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    from_feature_id uuid NOT NULL REFERENCES store.feature,
    to_feature_id   uuid NOT NULL REFERENCES store.feature,
    relation        text NOT NULL CHECK (relation IN ('same_as', 'overlaps', 'split_into', 'part_of', 'parallel_to')),
    status          text NOT NULL CHECK (status IN ('proposed', 'confirmed', 'rejected')),
    score           numeric CHECK (score BETWEEN 0 AND 1),   -- the proposer's confidence
    asserted_by     uuid NOT NULL REFERENCES store.agent,
    citation_id     uuid REFERENCES store.citation,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (from_feature_id <> to_feature_id)
);
CREATE INDEX feature_link_from ON store.feature_link (from_feature_id);
CREATE INDEX feature_link_to ON store.feature_link (to_feature_id);

-- Each relation fixes the feature kinds at its ends (model.md).
CREATE FUNCTION store.link_kinds() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    k_from text;
    k_to   text;
    want_from text;
    want_to   text;
BEGIN
    SELECT kind INTO k_from FROM store.feature WHERE id = NEW.from_feature_id;
    SELECT kind INTO k_to FROM store.feature WHERE id = NEW.to_feature_id;
    want_from := 'section';
    want_to := CASE NEW.relation WHEN 'part_of' THEN 'line' ELSE 'section' END;
    IF k_from <> want_from OR k_to <> want_to THEN
        RAISE EXCEPTION '% links a % to a %, not a % to a %',
            NEW.relation, want_from, want_to, k_from, k_to;
    END IF;
    IF TG_OP = 'UPDATE' AND (NEW.from_feature_id, NEW.to_feature_id, NEW.relation, NEW.score, NEW.asserted_by)
        IS DISTINCT FROM (OLD.from_feature_id, OLD.to_feature_id, OLD.relation, OLD.score, OLD.asserted_by) THEN
        RAISE EXCEPTION 'links are never edited in place; only their status moves';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER link_kinds BEFORE INSERT OR UPDATE ON store.feature_link
    FOR EACH ROW EXECUTE FUNCTION store.link_kinds();

-- Reviews --------------------------------------------------------------------------------

-- One table for verdicts on claims and on links (the API reviews both).
CREATE TABLE store.review (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id             uuid REFERENCES store.claim,
    link_id              uuid REFERENCES store.feature_link,
    reviewer_id          uuid NOT NULL REFERENCES store.agent,
    verdict              text NOT NULL,
    replaced_by_claim_id uuid REFERENCES store.claim,
    note                 text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    CHECK ((claim_id IS NULL) <> (link_id IS NULL)),
    CHECK (claim_id IS NULL OR verdict IN ('accept', 'reject', 'correct')),
    CHECK (link_id IS NULL OR verdict IN ('confirm', 'reject')),
    CHECK ((verdict = 'correct') = (replaced_by_claim_id IS NOT NULL))
);
CREATE INDEX review_claim ON store.review (claim_id);
CREATE INDEX review_link ON store.review (link_id);

-- External keys --------------------------------------------------------------------------

-- (system, key) -> a row, so an importer can run twice without duplicating (plan.md).
CREATE TABLE store.external_key (
    system    text NOT NULL,
    key       text NOT NULL,
    entity    text NOT NULL CHECK (entity IN ('agent', 'source', 'citation', 'feature', 'claim', 'link')),
    entity_id uuid NOT NULL,
    PRIMARY KEY (system, key)
);
CREATE INDEX external_key_entity ON store.external_key (entity_id);

-- Audit ----------------------------------------------------------------------------------

-- One history table for every table: old row, new row, operation, transaction, time and
-- agent. A write nobody can be named for is refused (plan.md, "The audit trail").
CREATE TABLE audit.history (
    id         bigserial PRIMARY KEY,
    table_name text NOT NULL,
    op         text NOT NULL,
    old_row    jsonb,
    new_row    jsonb,
    txid       bigint NOT NULL DEFAULT txid_current(),
    at         timestamptz NOT NULL DEFAULT now(),
    agent_id   uuid NOT NULL REFERENCES store.agent
);
CREATE INDEX history_row ON audit.history (table_name, ((coalesce(new_row, old_row) ->> 'id')));

CREATE FUNCTION audit.record() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    who uuid := store.current_agent();
BEGIN
    IF who IS NULL THEN
        RAISE EXCEPTION 'no agent for this write: set lineage.agent_id or map role % in store.agent_role',
            session_user;
    END IF;
    INSERT INTO audit.history (table_name, op, old_row, new_row, agent_id)
    VALUES (TG_TABLE_NAME, TG_OP,
            CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
            CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END,
            who);
    RETURN NULL;
END $$;

-- Seed: the attribute registry (model.md, "Attributes") and the store's own agent, which
-- registers people on their first login. Written before the audit triggers exist.
INSERT INTO store.attribute (key, value_type, cardinality, feature_kinds, networks, enum_values) VALUES
    ('name',           'text',     'one',  '{line,section,station}', NULL, NULL),
    ('owner',          'text',     'many', '{line,section,station}', NULL, NULL),
    ('operator',       'text',     'one',  '{line,section,station}', NULL, NULL),
    -- no allowed values yet, so every commodity claim is refused (docs/model.md, commodity)
    ('commodity',      'enum',     'one',  '{line,section}', NULL, '{}'),
    ('status',         'enum',     'one',  '{line,section,station,node,edge}', NULL,
        '{proposed,permitted,under_construction,operational,maintenance,decommissioned}'),
    ('route',          'geometry', 'one',  '{section}', NULL, NULL),
    ('location',       'geometry', 'one',  '{station,node}', NULL, NULL),
    ('diameter',       'quantity', 'one',  '{section}', '{gas,oil,refined,water}', NULL),
    ('voltage',        'quantity', 'one',  '{section}', '{electric}', NULL),
    ('gauge',          'quantity', 'one',  '{section}', '{road}', NULL),
    ('lanes',          'quantity', 'one',  '{section}', '{road}', NULL),
    ('station_kind',   'enum',     'one',  '{station}', NULL,
        '{pump,compressor,substation,terminal,intake,delivery}'),
    ('serves',         'feature',  'many', '{station}', NULL, NULL),
    ('flow_direction', 'enum',     'one',  '{edge}', NULL, '{forward,reverse,both}');

INSERT INTO store.agent (id, kind, name, pipeline)
VALUES ('00000000-0000-0000-0000-000000000001', 'pipeline', 'store', 'store');

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['agent', 'agent_role', 'agent_token', 'attribute', 'feature', 'line',
        'section', 'node', 'station', 'edge', 'edge_section', 'source', 'citation',
        'citation_request', 'claim', 'claim_citation', 'feature_link', 'review', 'external_key']
    LOOP
        EXECUTE format('CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON store.%I
                        FOR EACH ROW EXECUTE FUNCTION audit.record()', t);
    END LOOP;
END $$;

-- migrate:down

DROP SCHEMA audit CASCADE;
DROP SCHEMA store CASCADE;
