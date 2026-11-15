-- migrate:up

-- A section belongs to at most one line (scenarios, case 9): a line is the same pipe through
-- its life, and a sale or absorption is an owner or operator claim with dates, not a new
-- membership. Moving a section is a correction: the old link is rejected, the new one
-- confirmed, in one bundle. A line's label (its accepted name) is set by store's code.
CREATE UNIQUE INDEX feature_link_one_line ON store.feature_link (from_feature_id)
    WHERE relation = 'part_of' AND status = 'confirmed';

-- migrate:down

DROP INDEX store.feature_link_one_line;
