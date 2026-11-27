-- migrate:up

-- A section belongs to at most one line (scenarios, case 9): a line is the same pipe through
-- its life, and a sale or absorption is an owner or operator claim with dates, not a new
-- membership. Moving a section is a correction: the old link is rejected, the new one
-- confirmed, in one bundle. A line's label (its accepted name) is set by store's code.
--
-- A database written before this rule may hold a section confirmed into two lines. Which
-- membership is right is a person's decision, so nothing is chosen here: the migration
-- stops and says how to find them.
DO $$
DECLARE n int;
BEGIN
    SELECT count(*) INTO n FROM (
        SELECT from_feature_id FROM store.feature_link
        WHERE relation = 'part_of' AND status = 'confirmed'
        GROUP BY from_feature_id HAVING count(*) > 1) twice;
    IF n > 0 THEN
        RAISE EXCEPTION '% section(s) are confirmed part of more than one line. Decide which membership of each is right and set the status of the others to rejected in store.feature_link (the API before this change cannot reject a confirmed link), then migrate again. Find them with: SELECT from_feature_id, array_agg(id) FROM store.feature_link WHERE relation = ''part_of'' AND status = ''confirmed'' GROUP BY 1 HAVING count(*) > 1', n;
    END IF;
END $$;

CREATE UNIQUE INDEX feature_link_one_line ON store.feature_link (from_feature_id)
    WHERE relation = 'part_of' AND status = 'confirmed';

-- Lines named before store's code kept the label: give each its accepted name, by the same
-- rule as `lineage_store.writes.relabel`. As store's own agent, for the audit log.
SELECT set_config('lineage.agent_id', '00000000-0000-0000-0000-000000000001', true);
UPDATE store.feature f SET label = n.value_text
FROM (SELECT DISTINCT ON (c.feature_id) c.feature_id, c.value_text FROM store.claim c
      WHERE c.attribute_key = 'name' AND c.status = 'accepted'
      ORDER BY c.feature_id, coalesce(c.start_latest, c.start_earliest) DESC NULLS LAST,
               c.created_at DESC, c.id DESC) n
WHERE f.id = n.feature_id AND f.kind = 'line' AND f.label IS DISTINCT FROM n.value_text;

-- migrate:down

DROP INDEX store.feature_link_one_line;
