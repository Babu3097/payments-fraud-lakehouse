-- Gold: customers and merchants as an SCD Type 2 dimension, one row per version.
-- Step 1: Auto CDC turns the change events into history. Only segment and region create a new
-- version when they change. The version is valid from its own change time (__START_AT) until the
-- next one (__END_AT), a half-open range: start included, end excluded.
CREATE OR REFRESH PRIVATE STREAMING TABLE customer_history;

CREATE FLOW build_customer_history AS AUTO CDC INTO customer_history
FROM STREAM (workspace.silver.customer_profile_events)
KEYS (customer_id)
SEQUENCE BY changed_at
COLUMNS * EXCEPT (_source_file, _ingested_at)
STORED AS SCD TYPE 2
TRACK HISTORY ON segment, region;

-- Step 2: the dimension that reporting uses. The surrogate key is a deterministic hash of the
-- business key and the version start. A materialized view cannot use an identity column, and a hash
-- stays the same when the table is rebuilt (a sequence would not). The open end of the current
-- version becomes 9999-12-31, so a point-in-time join needs one range test, not a NULL test.
-- Key -1 is the Unknown member: PaySim has no customer master data, so its IDs map here (ADR-006).
CREATE OR REFRESH MATERIALIZED VIEW workspace.gold.dim_customer (
    CONSTRAINT customer_key_present EXPECT (customer_key IS NOT NULL) ON VIOLATION FAIL UPDATE,
    CONSTRAINT customer_id_present EXPECT (customer_id IS NOT NULL) ON VIOLATION FAIL UPDATE,
    CONSTRAINT valid_range EXPECT (valid_from < valid_to) ON VIOLATION FAIL UPDATE,
    CONSTRAINT known_entity_type EXPECT (entity_type IN ('customer', 'merchant', 'unknown'))
)
COMMENT 'Customer and merchant dimension, SCD Type 2 on segment and region, with an Unknown member'
AS
SELECT
    xxhash64(customer_id, cast(__start_at AS STRING)) AS customer_key,
    customer_id,
    entity_type,
    segment,
    region,
    __start_at AS valid_from,
    coalesce(__end_at, TIMESTAMP '9999-12-31 00:00:00') AS valid_to,
    __end_at IS NULL AS is_current
FROM customer_history

UNION ALL

SELECT
    -1 AS customer_key,
    'UNKNOWN' AS customer_id,
    'unknown' AS entity_type,
    'Unknown' AS segment,
    'Unknown' AS region,
    TIMESTAMP '1970-01-01 00:00:00' AS valid_from,
    TIMESTAMP '9999-12-31 00:00:00' AS valid_to,
    TRUE AS is_current
