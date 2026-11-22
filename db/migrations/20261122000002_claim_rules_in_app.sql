-- migrate:up

-- What a claim's value may be is checked by store's code now, from the attribute registry in
-- lineage_model (`claim_problem`, called by every claim write), not by a trigger. The same
-- rules, in the same words; the registry table stays as the reference claims point at.
DROP TRIGGER claim_fits_attribute ON store.claim;
DROP FUNCTION store.claim_fits_attribute();

-- migrate:down

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
