-- migrate:up

-- What a quantity may be, beside its type (docs/model.md, "Attributes"): the units it may be
-- given in, and a lower bound it must be above. Checked for every writer (the API, QGIS,
-- importers) by the same trigger that checks the type, so a client's own checks (Studio,
-- the Agent: both read the registry as `lineage_model.attributes.contract()` writes it) can
-- never be looser than store.
-- Claims already written are left as they are: claims are never edited in place.
ALTER TABLE store.attribute
    ADD COLUMN units     text[],   -- quantity only; NULL: any unit, or none
    ADD COLUMN min_value numeric;  -- quantity only; the value must be above it

-- Written as store's own agent, like the seed: every write names one.
SELECT set_config('lineage.agent_id', '00000000-0000-0000-0000-000000000001', true);
UPDATE store.attribute SET units = '{in,mm}', min_value = 0 WHERE key = 'diameter';
UPDATE store.attribute SET min_value = 0 WHERE key IN ('voltage', 'gauge', 'lanes');

CREATE OR REPLACE FUNCTION store.claim_fits_attribute() RETURNS trigger
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
    IF a.value_type = 'quantity' AND a.units IS NOT NULL
       AND (NEW.value_unit IS NULL OR NOT NEW.value_unit = ANY (a.units)) THEN
        RAISE EXCEPTION '% needs a unit, one of (%)', a.key, array_to_string(a.units, ', ');
    END IF;
    IF a.value_type = 'quantity' AND a.min_value IS NOT NULL AND NEW.value_num <= a.min_value THEN
        RAISE EXCEPTION '% must be more than %', a.key, a.min_value;
    END IF;
    RETURN NEW;
END $$;

-- migrate:down

CREATE OR REPLACE FUNCTION store.claim_fits_attribute() RETURNS trigger
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

ALTER TABLE store.attribute DROP COLUMN units, DROP COLUMN min_value;
