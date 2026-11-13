-- migrate:up

-- `commodity` is reserved: no values, so the store refuses every commodity claim for now.
-- The network already says what a line carries in nearly every case. If we use it, it is
-- for what the network doesn't distinguish: natural versus manufactured gas on a gas line
-- (town gas lines converted in the 1940s and 50s, as two dated claims), or crude versus
-- products on an oil line. Agree the values first, then give them here and in
-- lineage_model.attributes.
-- Written by the store's own agent (every store write is audited with one).
SELECT set_config('lineage.agent_id', '00000000-0000-0000-0000-000000000001', true);
UPDATE store.attribute SET enum_values = '{}' WHERE key = 'commodity';
COMMENT ON TABLE store.attribute IS
    'Attribute registry (docs/model.md, Attributes). An enum with no values is reserved: no claims accepted.';

-- migrate:down

SELECT set_config('lineage.agent_id', '00000000-0000-0000-0000-000000000001', true);
UPDATE store.attribute
SET enum_values = '{natural_gas,manufactured_gas,crude_oil,refined_products,water,electricity}'
WHERE key = 'commodity';
COMMENT ON TABLE store.attribute IS NULL;
