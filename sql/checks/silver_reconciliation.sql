-- Silver reconciliation: every silver table must agree with bronze and the generator manifests.
-- One row per check: (check_name, expected, actual, passed). passed = false is a failure.
-- Each defect class the generator injected must show up in quarantine in exactly the same number.
WITH manifests AS (
    SELECT
        raw_manifest.clean_rows,
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

expected_valid AS (
    SELECT
        feed_date,
        clean_rows - null_field - bad_amount - unknown_type - bad_timestamp AS valid_rows
    FROM manifests
),

silver_days AS (
    SELECT
        event_date AS feed_date,
        count(*) AS silver_rows
    FROM workspace.silver.transactions
    WHERE source_system = 'generator-v1'
    GROUP BY event_date
),

reasons AS (
    SELECT explode(failed_checks) AS reason
    FROM workspace.silver.transactions_quarantine
),

checks AS (
    SELECT
        'silver: total rows equal PaySim rows plus valid generated rows' AS check_name,
        (SELECT count(*) FROM workspace.bronze.paysim_transactions)
        + (SELECT sum(valid_rows) FROM expected_valid) AS expected,
        (SELECT count(*) FROM workspace.silver.transactions) AS actual
    UNION ALL
    SELECT
        'silver: PaySim rows equal bronze' AS check_name,
        (SELECT count(*) FROM workspace.bronze.paysim_transactions) AS expected,
        count_if(source_system = 'paysim') AS actual
    FROM workspace.silver.transactions
    UNION ALL
    SELECT
        'silver: days loaded equal days in the manifests' AS check_name,
        (SELECT count(*) FROM manifests) AS expected,
        count(*) AS actual
    FROM silver_days
    UNION ALL
    SELECT
        'silver: days whose valid rows differ from the manifest' AS check_name,
        0 AS expected,
        count_if(s.silver_rows <> e.valid_rows) AS actual
    FROM silver_days AS s
    INNER JOIN expected_valid AS e ON s.feed_date = e.feed_date
    UNION ALL
    SELECT
        'silver: event_id is unique' AS check_name,
        0 AS expected,
        count(*) - count(DISTINCT event_id) AS actual
    FROM workspace.silver.transactions
    UNION ALL
    SELECT
        'silver and quarantine share no event_id' AS check_name,
        0 AS expected,
        count(*) AS actual
    FROM workspace.silver.transactions AS s
    INNER JOIN workspace.silver.transactions_quarantine AS q ON s.event_id = q.event_id
    UNION ALL
    SELECT
        'quarantine: rows equal the injected defects' AS check_name,
        (SELECT sum(null_field + bad_amount + unknown_type + bad_timestamp) FROM manifests)
            AS expected,
        (SELECT count(*) FROM workspace.silver.transactions_quarantine) AS actual
    UNION ALL
    SELECT
        'quarantine: NULL_REQUIRED_FIELD rows equal the manifests' AS check_name,
        (SELECT sum(m.null_field) FROM manifests AS m) AS expected,
        count_if(reason = 'NULL_REQUIRED_FIELD') AS actual
    FROM reasons
    UNION ALL
    SELECT
        'quarantine: NEGATIVE_AMOUNT rows equal the manifests' AS check_name,
        (SELECT sum(m.bad_amount) FROM manifests AS m) AS expected,
        count_if(reason = 'NEGATIVE_AMOUNT') AS actual
    FROM reasons
    UNION ALL
    SELECT
        'quarantine: UNKNOWN_TYPE rows equal the manifests' AS check_name,
        (SELECT sum(m.unknown_type) FROM manifests AS m) AS expected,
        count_if(reason = 'UNKNOWN_TYPE') AS actual
    FROM reasons
    UNION ALL
    SELECT
        'quarantine: BAD_TIMESTAMP rows equal the manifests' AS check_name,
        (SELECT sum(m.bad_timestamp) FROM manifests AS m) AS expected,
        count_if(reason = 'BAD_TIMESTAMP') AS actual
    FROM reasons
    UNION ALL
    SELECT
        'quarantine: UNKNOWN_STATUS rows (the generator injects none)' AS check_name,
        0 AS expected,
        count_if(reason = 'UNKNOWN_STATUS') AS actual
    FROM reasons
    UNION ALL
    SELECT
        'quarantine: rows with more than one reason' AS check_name,
        0 AS expected,
        count_if(size(failed_checks) > 1) AS actual
    FROM workspace.silver.transactions_quarantine
    UNION ALL
    SELECT
        'quarantine: rows without a raw record' AS check_name,
        0 AS expected,
        count_if(raw_record IS null) AS actual
    FROM workspace.silver.transactions_quarantine
    UNION ALL
    SELECT
        'duplicates removed equal the manifests' AS check_name,
        (SELECT sum(duplicates) FROM manifests) AS expected,
        (SELECT count(*) FROM workspace.bronze.transactions_daily)
        - (
            SELECT count(*) FROM workspace.silver.transactions
            WHERE source_system = 'generator-v1'
        )
        - (SELECT count(*) FROM workspace.silver.transactions_quarantine) AS actual
    UNION ALL
    SELECT
        'paysim: declined rows equal the flagged rows' AS check_name,
        (SELECT sum(b.isflaggedfraud) FROM workspace.bronze.paysim_transactions AS b) AS expected,
        count_if(source_system = 'paysim' AND status = 'DECLINED') AS actual
    FROM workspace.silver.transactions
    UNION ALL
    SELECT
        'paysim: zero-amount rows are kept and flagged' AS check_name,
        (SELECT count_if(b.amount = 0) FROM workspace.bronze.paysim_transactions AS b) AS expected,
        count_if(source_system = 'paysim' AND is_zero_amount) AS actual
    FROM workspace.silver.transactions
    UNION ALL
    SELECT
        'paysim: merchant destination balances are NULL, not zero' AS check_name,
        0 AS expected,
        count_if(
            source_system = 'paysim'
            AND counterparty_kind = 'merchant'
            AND dest_balance_before IS NOT null
        ) AS actual
    FROM workspace.silver.transactions
    UNION ALL
    SELECT
        'paysim: first event time is the anchor' AS check_name,
        1 AS expected,
        cast(min(event_ts) = TIMESTAMP '2026-08-20 00:00:00' AS INT) AS actual
    FROM workspace.silver.transactions
    WHERE source_system = 'paysim'
    UNION ALL
    SELECT
        'paysim: last event time is step 743' AS check_name,
        1 AS expected,
        cast(max(event_ts) = TIMESTAMP '2026-09-19 22:00:00' AS INT) AS actual
    FROM workspace.silver.transactions
    WHERE source_system = 'paysim'
    UNION ALL
    SELECT
        'paysim: bank holiday rows equal the rows on 31 August' AS check_name,
        (
            SELECT count(*)
            FROM workspace.bronze.paysim_transactions AS b
            WHERE b.step BETWEEN 265 AND 288
        ) AS expected,
        count_if(is_bank_holiday) AS actual
    FROM workspace.silver.transactions
    WHERE source_system = 'paysim'
    UNION ALL
    SELECT
        'status is derived for PaySim only' AS check_name,
        0 AS expected,
        count_if(status_source = 'derived_paysim_rule' AND source_system <> 'paysim') AS actual
    FROM workspace.silver.transactions
    UNION ALL
    SELECT
        'customer profile: rows equal bronze' AS check_name,
        (SELECT count(*) FROM workspace.bronze.customer_profile_changes) AS expected,
        (SELECT count(*) FROM workspace.silver.customer_profile_events) AS actual
    UNION ALL
    SELECT
        'bank holidays: regions' AS check_name,
        3 AS expected,
        count(DISTINCT division) AS actual
    FROM workspace.silver.bank_holidays
    UNION ALL
    SELECT
        'bank holidays: duplicate holidays' AS check_name,
        0 AS expected,
        count(*) - count(DISTINCT division, holiday_date, title) AS actual
    FROM workspace.silver.bank_holidays
    UNION ALL
    SELECT
        'bank holidays: England and Wales has the 2026 summer bank holiday' AS check_name,
        1 AS expected,
        count_if(division = 'england-and-wales' AND holiday_date = DATE '2026-08-31') AS actual
    FROM workspace.silver.bank_holidays
)

SELECT
    check_name,
    expected,
    actual,
    expected = actual AS passed
FROM checks
