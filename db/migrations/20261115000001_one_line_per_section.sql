-- migrate:up

-- A section belongs to at most one line (scenarios, case 9): a line is the same pipe through
-- its life, and a sale or absorption is an owner or operator claim with dates, not a new
-- membership. Moving a section is a correction: the old link is rejected, the new one
-- confirmed, in one bundle.
CREATE UNIQUE INDEX feature_link_one_line ON store.feature_link (from_feature_id)
    WHERE relation = 'part_of' AND status = 'confirmed';

-- A line's label is its accepted name (scenarios, case 9): the one in force latest, then the
-- one written last. Recomputed whenever a name claim is written or decided, by any route
-- (the API, QGIS). A line with no accepted name keeps no label.
CREATE FUNCTION store.line_label() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE store.feature f SET label = (
        SELECT c.value_text FROM store.claim c
        WHERE c.feature_id = f.id AND c.attribute_key = 'name' AND c.status = 'accepted'
        ORDER BY coalesce(c.start_latest, c.start_earliest) DESC NULLS LAST, c.created_at DESC
        LIMIT 1)
    WHERE f.id = NEW.feature_id AND f.kind = 'line';
    RETURN NULL;
END $$;
CREATE TRIGGER line_label AFTER INSERT OR UPDATE OF status ON store.claim
    FOR EACH ROW WHEN (NEW.attribute_key = 'name') EXECUTE FUNCTION store.line_label();

-- migrate:down

DROP TRIGGER line_label ON store.claim;
DROP FUNCTION store.line_label();
DROP INDEX store.feature_link_one_line;
