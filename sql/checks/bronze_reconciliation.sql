-- Bronze reconciliation: every bronze table must agree with the files that fed it.
-- One row per check: (check_name, expected, actual, passed). passed = false is a failure.
-- The generator's manifests are the ground truth for the daily feeds (docs/generator.md).
WITH manifests AS (
    SELECT
        raw_manifest.rows_written,
        raw_manifest.clean_rows,
        raw_manifest.profile_rows,
        raw_manifest.defects.null_field,
        raw_manifest.defects.bad_amount,
        raw_manifest.defects.unknown_type,
        raw_manifest.defects.bad_timestamp,
        raw_manifest.defects.duplicates,
        to_date(raw_manifest.`date`) AS feed_date
    FROM
        read_files(
            '/Volumes/workspace/bronze/landing/_manifests/', format => 'json', multiline => true
        ) AS raw_manifest
),

transactions AS (
    SELECT
        to_date(left(substring_index(_source_file, 'transactions_', -1), 10)) AS feed_date,
        count(*) AS bronze_rows,
        count(DISTINCT event_id) AS distinct_events,
        count_if(customer_id IS null OR amount IS null OR type IS null OR event_ts IS null)
            AS null_field,
        count_if(amount < 0) AS bad_amount,
        count_if(
            type IS NOT null AND type NOT IN ('CASH_OUT', 'PAYMENT', 'CASH_IN', 'TRANSFER', 'DEBIT')
        ) AS unknown_type,
        count_if(event_ts IS NOT null AND try_to_timestamp(event_ts) IS null) AS bad_timestamp
    FROM workspace.bronze.transactions_daily
    GROUP BY 1
),

profile AS (
    SELECT
        to_date(left(substring_index(_source_file, 'customer_profile_', -1), 10)) AS feed_date,
        count(*) AS bronze_rows
    FROM workspace.bronze.customer_profile_changes
    WHERE NOT endswith(_source_file, '_late.csv')
    GROUP BY 1
),

late_landing AS (
    -- Late-arriving files have no manifest, so their rows are counted in the landing folder itself.
    SELECT count(*) AS late_rows
    FROM (
        SELECT _metadata.file_path
        FROM read_files(
            '/Volumes/workspace/bronze/landing/customer_profile/', format => 'csv', header => true
        )
    ) AS landing
    WHERE endswith(landing.file_path, '_late.csv')
),

checks AS (
    SELECT
        'paysim: row count equals the source file' AS check_name,
        6362620 AS expected,
        count(*) AS actual
    FROM workspace.bronze.paysim_transactions
    UNION ALL
    SELECT
        'paysim: fraud rows equal the source' AS check_name,
        8213 AS expected,
        sum(isfraud) AS actual
    FROM workspace.bronze.paysim_transactions
    UNION ALL
    SELECT
        'paysim: flagged rows equal the source' AS check_name,
        16 AS expected,
        sum(isflaggedfraud) AS actual
    FROM workspace.bronze.paysim_transactions
    UNION ALL
    SELECT
        'paysim: rows rescued by the contract' AS check_name,
        0 AS expected,
        count_if(_rescued_data IS NOT null) AS actual
    FROM workspace.bronze.paysim_transactions
    UNION ALL
    SELECT
        'paysim: money values rejected as null' AS check_name,
        0 AS expected,
        count_if(
            amount IS null
            OR oldbalanceorg IS null
            OR newbalanceorig IS null
            OR oldbalancedest IS null
            OR newbalancedest IS null
        ) AS actual
    FROM workspace.bronze.paysim_transactions
    UNION ALL
    SELECT
        'transactions: days loaded equal days in the manifests' AS check_name,
        (SELECT count(*) FROM manifests) AS expected,
        count(*) AS actual
    FROM transactions
    UNION ALL
    SELECT
        'transactions: days whose line count differs from the manifest' AS check_name,
        0 AS expected,
        count_if(t.bronze_rows <> m.rows_written) AS actual
    FROM transactions AS t
    INNER JOIN manifests AS m ON t.feed_date = m.feed_date
    UNION ALL
    SELECT
        'transactions: days whose distinct events differ from the manifest' AS check_name,
        0 AS expected,
        count_if(t.distinct_events <> m.clean_rows) AS actual
    FROM transactions AS t
    INNER JOIN manifests AS m ON t.feed_date = m.feed_date
    UNION ALL
    SELECT
        'transactions: days whose null defects differ from the manifest' AS check_name,
        0 AS expected,
        count_if(t.null_field <> m.null_field) AS actual
    FROM transactions AS t
    INNER JOIN manifests AS m ON t.feed_date = m.feed_date
    UNION ALL
    SELECT
        'transactions: days whose bad amounts differ from the manifest' AS check_name,
        0 AS expected,
        count_if(t.bad_amount <> m.bad_amount) AS actual
    FROM transactions AS t
    INNER JOIN manifests AS m ON t.feed_date = m.feed_date
    UNION ALL
    SELECT
        'transactions: days whose unknown types differ from the manifest' AS check_name,
        0 AS expected,
        count_if(t.unknown_type <> m.unknown_type) AS actual
    FROM transactions AS t
    INNER JOIN manifests AS m ON t.feed_date = m.feed_date
    UNION ALL
    SELECT
        'transactions: days whose bad timestamps differ from the manifest' AS check_name,
        0 AS expected,
        count_if(t.bad_timestamp <> m.bad_timestamp) AS actual
    FROM transactions AS t
    INNER JOIN manifests AS m ON t.feed_date = m.feed_date
    UNION ALL
    SELECT
        'transactions: rows rescued by the contract' AS check_name,
        0 AS expected,
        count_if(_rescued_data IS NOT null) AS actual
    FROM workspace.bronze.transactions_daily
    UNION ALL
    SELECT
        'customer profile: total rows equal the manifests plus the late files' AS check_name,
        (SELECT sum(m.profile_rows) FROM manifests AS m)
        + (SELECT l.late_rows FROM late_landing AS l) AS expected,
        count(*) AS actual
    FROM workspace.bronze.customer_profile_changes
    UNION ALL
    SELECT
        'customer profile: late-arriving rows equal the landing files' AS check_name,
        (SELECT l.late_rows FROM late_landing AS l) AS expected,
        count_if(endswith(_source_file, '_late.csv')) AS actual
    FROM workspace.bronze.customer_profile_changes
    UNION ALL
    SELECT
        'customer profile: days whose row count differs from the manifest' AS check_name,
        0 AS expected,
        count_if(p.bronze_rows <> m.profile_rows) AS actual
    FROM profile AS p
    INNER JOIN manifests AS m ON p.feed_date = m.feed_date
    UNION ALL
    SELECT
        'customer profile: rows rescued by the contract' AS check_name,
        0 AS expected,
        count_if(_rescued_data IS NOT null) AS actual
    FROM workspace.bronze.customer_profile_changes
    UNION ALL
    SELECT
        'bank holidays: payloads landed' AS check_name,
        1 AS expected,
        count(*) AS actual
    FROM workspace.bronze.bank_holidays_raw
    UNION ALL
    SELECT
        'bank holidays: regions parsed from the payload' AS check_name,
        3 AS expected,
        max(
            size(
                from_json(
                    payload,
                    'MAP<STRING, STRUCT<events: ARRAY<STRUCT<date: STRING>>>>'
                )
            )
        ) AS actual
    FROM workspace.bronze.bank_holidays_raw
)

SELECT
    check_name,
    expected,
    actual,
    expected = actual AS passed
FROM checks
