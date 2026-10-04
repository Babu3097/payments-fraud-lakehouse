-- Gold reconciliation: control totals that compare gold with the raw data in bronze.
-- One row, many named constraints. Every FAIL UPDATE constraint stops the whole update if it is
-- false, so a mismatch can never go unnoticed, and the event log names the check that failed
-- (ADR-017). Money is compared to the cent: all amounts are DECIMAL.
--
-- PaySim:    gold rows, total amount and fraud count equal bronze.
-- Generated: every distinct raw event is in exactly one place, gold or quarantine, and the amounts
--            add up the same way (duplicates are removed, so they are counted once).
-- Model:     keys resolve, the fact has one row per event, the KPI table adds up to the fact.
CREATE OR REFRESH MATERIALIZED VIEW workspace.gold.reconciliation (
    CONSTRAINT paysim_rows_match EXPECT (paysim_rows_gold = paysim_rows_bronze)
    ON VIOLATION FAIL UPDATE,
    CONSTRAINT paysim_amount_matches EXPECT (paysim_amount_gold = paysim_amount_bronze)
    ON VIOLATION FAIL UPDATE,
    CONSTRAINT paysim_fraud_matches EXPECT (paysim_fraud_gold = paysim_fraud_bronze)
    ON VIOLATION FAIL UPDATE,
    CONSTRAINT generated_events_accounted_for EXPECT (
        bronze_generated_events = gold_generated_rows + quarantined_events
    ) ON VIOLATION FAIL UPDATE,
    CONSTRAINT generated_amount_accounted_for EXPECT (
        bronze_generated_amount = gold_generated_amount + quarantined_amount
    ) ON VIOLATION FAIL UPDATE,
    CONSTRAINT fact_equals_silver EXPECT (fact_rows = silver_rows) ON VIOLATION FAIL UPDATE,
    CONSTRAINT fact_event_id_unique EXPECT (fact_rows = fact_distinct_events)
    ON VIOLATION FAIL UPDATE,
    CONSTRAINT no_orphan_keys EXPECT (
        orphan_date_keys = 0
        AND orphan_type_keys = 0
        AND orphan_origin_keys = 0
        AND orphan_counterparty_keys = 0
    ) ON VIOLATION FAIL UPDATE,
    CONSTRAINT generated_customers_resolved EXPECT (generated_unknown_customer_rows = 0)
    ON VIOLATION FAIL UPDATE,
    CONSTRAINT kpi_daily_matches_fact EXPECT (
        kpi_txn_total = fact_rows AND kpi_value_total = fact_value_total
    ) ON VIOLATION FAIL UPDATE,
    CONSTRAINT one_current_version_per_customer EXPECT (current_versions = distinct_customers)
    ON VIOLATION FAIL UPDATE,
    CONSTRAINT versions_match_profile_events EXPECT (dim_versions = profile_events)
)
COMMENT 'Control totals comparing gold with bronze; any mismatch fails the update'
AS
SELECT
    (
        SELECT count(*) FROM workspace.gold.fact_transactions
        WHERE source_system = 'paysim')
        AS paysim_rows_gold,
    (SELECT count(*) FROM workspace.bronze.paysim_transactions) AS paysim_rows_bronze,
    (
        SELECT coalesce(sum(amount), 0) FROM workspace.gold.fact_transactions
        WHERE source_system = 'paysim'
    )
        AS paysim_amount_gold,
    (SELECT coalesce(sum(amount), 0) FROM workspace.bronze.paysim_transactions)
        AS paysim_amount_bronze,
    (
        SELECT count_if(is_fraud) FROM workspace.gold.fact_transactions
        WHERE source_system = 'paysim')
        AS paysim_fraud_gold,
    (SELECT coalesce(sum(isfraud), 0) FROM workspace.bronze.paysim_transactions)
        AS paysim_fraud_bronze,
    (SELECT count(DISTINCT event_id) FROM workspace.bronze.transactions_daily)
        AS bronze_generated_events,
    (
        SELECT coalesce(sum(a.amount), 0)
        FROM (
            SELECT max(amount) AS amount
            FROM workspace.bronze.transactions_daily
            GROUP BY event_id
        ) AS a
    ) AS bronze_generated_amount,
    (
        SELECT count(*) FROM workspace.gold.fact_transactions
        WHERE source_system = 'generator-v1')
        AS gold_generated_rows,
    (
        SELECT coalesce(sum(amount), 0) FROM workspace.gold.fact_transactions
        WHERE source_system = 'generator-v1'
    )
        AS gold_generated_amount,
    (SELECT count(DISTINCT event_id) FROM workspace.silver.transactions_quarantine)
        AS quarantined_events,
    (
        SELECT coalesce(sum(cast(get_json_object(q.raw_record, '$.amount') AS DECIMAL(18, 2))), 0)
        FROM (
            -- The first time each event was quarantined. Plain row_number and a filter, not
            -- QUALIFY, so the same SQL runs in open-source Spark (the unit tests need that).
            SELECT raw_record
            FROM (
                SELECT
                    raw_record,
                    row_number() OVER (PARTITION BY event_id ORDER BY quarantined_at) AS seen_order
                FROM workspace.silver.transactions_quarantine
            ) AS ranked
            WHERE seen_order = 1
        ) AS q
    ) AS quarantined_amount,
    (SELECT count(*) FROM workspace.silver.transactions) AS silver_rows,
    (SELECT count(*) FROM workspace.gold.fact_transactions) AS fact_rows,
    (SELECT count(DISTINCT event_id) FROM workspace.gold.fact_transactions) AS fact_distinct_events,
    (SELECT coalesce(sum(amount), 0) FROM workspace.gold.fact_transactions) AS fact_value_total,
    (
        SELECT count(*)
        FROM workspace.gold.fact_transactions AS f
        LEFT ANTI JOIN workspace.gold.dim_date AS d ON f.date_key = d.date_key
    ) AS orphan_date_keys,
    (
        SELECT count(*)
        FROM workspace.gold.fact_transactions AS f
        LEFT ANTI JOIN workspace.gold.dim_type AS t ON f.type_key = t.type_key
    ) AS orphan_type_keys,
    (
        SELECT count(*)
        FROM workspace.gold.fact_transactions AS f
        LEFT ANTI JOIN workspace.gold.dim_customer AS c ON f.origin_customer_key = c.customer_key
    ) AS orphan_origin_keys,
    (
        SELECT count(*)
        FROM workspace.gold.fact_transactions AS f
        LEFT ANTI JOIN workspace.gold.dim_customer AS c ON f.counterparty_key = c.customer_key
    ) AS orphan_counterparty_keys,
    (
        SELECT count(*)
        FROM workspace.gold.fact_transactions
        WHERE source_system = 'generator-v1' AND (origin_customer_key = -1 OR counterparty_key = -1)
    ) AS generated_unknown_customer_rows,
    (SELECT coalesce(sum(txn_count), 0) FROM workspace.gold.kpi_daily) AS kpi_txn_total,
    (SELECT coalesce(sum(txn_value), 0) FROM workspace.gold.kpi_daily) AS kpi_value_total,
    (
        SELECT count(*) FROM workspace.gold.dim_customer
        WHERE is_current AND customer_key <> -1)
        AS current_versions,
    (
        SELECT count(DISTINCT customer_id) FROM workspace.gold.dim_customer
        WHERE customer_key <> -1)
        AS distinct_customers,
    (
        SELECT count(*) FROM workspace.gold.dim_customer
        WHERE customer_key <> -1
    ) AS dim_versions,
    (SELECT count(*) FROM workspace.silver.customer_profile_events) AS profile_events
