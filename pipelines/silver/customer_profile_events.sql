-- Silver: customer and merchant change events, typed and validated. Phase 4 turns these into the
-- SCD Type 2 dimension. This feed has no injected defects, so it uses expectations only: a missing
-- key or sequence value fails the update (it would break the history), the rest is measured.
CREATE OR REFRESH STREAMING TABLE workspace.silver.customer_profile_events (
    CONSTRAINT customer_id_present EXPECT (customer_id IS NOT NULL) ON VIOLATION FAIL UPDATE,
    CONSTRAINT changed_at_present EXPECT (changed_at IS NOT NULL) ON VIOLATION FAIL UPDATE,
    CONSTRAINT known_entity_type EXPECT (entity_type IN ('customer', 'merchant')),
    CONSTRAINT id_prefix_matches_type EXPECT (
        (entity_type = 'customer' AND customer_id LIKE 'CG%')
        OR (entity_type = 'merchant' AND customer_id LIKE 'MG%')
    ),
    CONSTRAINT segment_present EXPECT (segment IS NOT NULL),
    CONSTRAINT region_present EXPECT (region IS NOT NULL)
)
COMMENT 'Customer and merchant attribute change events, one row per change'
AS
SELECT
    customer_id,
    entity_type,
    segment,
    region,
    changed_at,
    _source_file,
    _ingested_at
FROM STREAM (workspace.bronze.customer_profile_changes)
