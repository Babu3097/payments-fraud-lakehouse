-- Gold checks, run after the pipeline. They go beyond the reconciliation view inside the pipeline:
-- the SCD2 point-in-time test recomputes each customer's segment and region at event time straight
-- from the change events, without Auto CDC, and the control totals come from the landing files
-- themselves, not from bronze. One row per check: (check_name, expected, actual, passed).
WITH versions AS (
    SELECT
        customer_id,
        valid_from,
        valid_to,
        is_current,
        lead(valid_from) OVER (PARTITION BY customer_id ORDER BY valid_from) AS next_from
    FROM workspace.gold.dim_customer
    WHERE customer_key <> -1
),

as_of AS (
    SELECT
        f.event_id,
        max_by(e.segment, e.changed_at) AS expected_segment,
        max_by(e.region, e.changed_at) AS expected_region
    FROM workspace.gold.fact_transactions AS f
    INNER JOIN workspace.silver.customer_profile_events AS e
        ON f.customer_id = e.customer_id AND f.event_ts >= e.changed_at
    WHERE f.source_system = 'generator-v1'
    GROUP BY f.event_id
),

actual AS (
    SELECT
        f.event_id,
        c.segment AS actual_segment,
        c.region AS actual_region,
        c.is_current
    FROM workspace.gold.fact_transactions AS f
    INNER JOIN workspace.gold.dim_customer AS c ON f.origin_customer_key = c.customer_key
    WHERE f.source_system = 'generator-v1'
),

checks AS (
    SELECT
        'scd2: versions with a gap or overlap before the next one' AS check_name,
        0 AS expected,
        count_if(next_from IS NOT NULL AND valid_to <> next_from) AS actual
    FROM versions
    UNION ALL
    SELECT
        'scd2: versions that end before they start' AS check_name,
        0 AS expected,
        count_if(valid_to <= valid_from) AS actual
    FROM versions
    UNION ALL
    SELECT
        'scd2: customers without exactly one current version' AS check_name,
        0 AS expected,
        count(*) AS actual
    FROM (
        SELECT customer_id
        FROM versions
        GROUP BY customer_id
        HAVING count_if(is_current) <> 1
    ) AS bad
    UNION ALL
    SELECT
        'scd2: generated facts with no as-of match' AS check_name,
        0 AS expected,
        count(*) AS actual
    FROM actual AS a
    LEFT ANTI JOIN as_of AS o ON a.event_id = o.event_id
    UNION ALL
    SELECT
        'scd2: point-in-time mismatches (segment or region)' AS check_name,
        0 AS expected,
        count_if(
            o.expected_segment <> a.actual_segment OR o.expected_region <> a.actual_region
        ) AS actual
    FROM as_of AS o
    INNER JOIN actual AS a ON o.event_id = a.event_id
    UNION ALL
    SELECT
        'scd2: history is used (some facts point at an older version)' AS check_name,
        1 AS expected,
        cast(count_if(NOT is_current) > 0 AS INT) AS actual
    FROM actual
    UNION ALL
    SELECT
        'dim_customer: Unknown member rows' AS check_name,
        1 AS expected,
        count(*) AS actual
    FROM workspace.gold.dim_customer
    WHERE customer_key = -1
    UNION ALL
    SELECT
        'dim_date: calendar days' AS check_name,
        1096 AS expected,
        count(*) AS actual
    FROM workspace.gold.dim_date
    UNION ALL
    SELECT
        'dim_date: England and Wales bank holidays in 2026' AS check_name,
        8 AS expected,
        count_if(is_bank_holiday_eaw AND calendar_year = 2026) AS actual
    FROM workspace.gold.dim_date
    UNION ALL
    SELECT
        'dim_date: 31 August is the England and Wales summer bank holiday' AS check_name,
        1 AS expected,
        count_if(date_key = 20260831 AND is_bank_holiday_eaw) AS actual
    FROM workspace.gold.dim_date
    UNION ALL
    SELECT
        'dim_type: rows' AS check_name,
        5 AS expected,
        count(*) AS actual
    FROM workspace.gold.dim_type
    UNION ALL
    SELECT
        'fact: Unknown customers outside PaySim' AS check_name,
        0 AS expected,
        count_if(source_system <> 'paysim' AND origin_customer_key = -1) AS actual
    FROM workspace.gold.fact_transactions
    UNION ALL
    SELECT
        'fact: PaySim rows with a known customer' AS check_name,
        0 AS expected,
        count_if(source_system = 'paysim' AND origin_customer_key <> -1) AS actual
    FROM workspace.gold.fact_transactions
    UNION ALL
    SELECT
        'fact: rows without an amount band' AS check_name,
        0 AS expected,
        count_if(amount_band IS NULL) AS actual
    FROM workspace.gold.fact_transactions
    UNION ALL
    SELECT
        'landing file: PaySim rows equal gold' AS check_name,
        (
            SELECT count(*)
            FROM
                read_files(
                    '/Volumes/workspace/bronze/landing/paysim/', format => 'csv', header => TRUE
                )
        ) AS expected,
        count_if(source_system = 'paysim') AS actual
    FROM workspace.gold.fact_transactions
    UNION ALL
    SELECT
        'landing file: PaySim total amount equals gold, to the cent' AS check_name,
        (
            SELECT sum(cast(r.amount AS DECIMAL(18, 2)))
            FROM read_files(
                '/Volumes/workspace/bronze/landing/paysim/',
                format => 'csv', header => TRUE, schemahints => 'amount STRING'
            ) AS r
        ) AS expected,
        sum(CASE WHEN source_system = 'paysim' THEN amount END) AS actual
    FROM workspace.gold.fact_transactions
    UNION ALL
    SELECT
        'landing files: generated events are in gold or quarantine' AS check_name,
        (
            SELECT count(DISTINCT r.event_id)
            FROM
                read_files(
                    '/Volumes/workspace/bronze/landing/transactions_daily/', format => 'json'
                ) AS r
        ) AS expected,
        count_if(source_system = 'generator-v1')
        + (SELECT count(DISTINCT q.event_id) FROM workspace.silver.transactions_quarantine AS q)
            AS actual
    FROM workspace.gold.fact_transactions
    UNION ALL
    SELECT
        'kpi_daily: one row per date and source' AS check_name,
        0 AS expected,
        count(*) - count(DISTINCT date_key, source_system) AS actual
    FROM workspace.gold.kpi_daily
    UNION ALL
    SELECT
        'kpi_daily: rates outside 0 to 1' AS check_name,
        0 AS expected,
        count_if(approval_rate NOT BETWEEN 0 AND 1 OR fraud_rate NOT BETWEEN 0 AND 1) AS actual
    FROM workspace.gold.kpi_daily
    UNION ALL
    SELECT
        'rules: PaySim balance-drain flags equal the profile (8,018)' AS check_name,
        8018 AS expected,
        count_if(flag_balance_drain) AS actual
    FROM workspace.gold.fact_transactions
    WHERE source_system = 'paysim'
    UNION ALL
    SELECT
        'rules: PaySim source-rule flags equal the profile (16)' AS check_name,
        16 AS expected,
        count_if(flag_source_rule) AS actual
    FROM workspace.gold.fact_transactions
    WHERE source_system = 'paysim'
    UNION ALL
    SELECT
        'unusual_activity: rows without a reason' AS check_name,
        0 AS expected,
        count_if(size(flag_reasons) = 0) AS actual
    FROM workspace.gold.unusual_activity
)

SELECT
    check_name,
    expected,
    actual,
    expected = actual AS passed
FROM checks
