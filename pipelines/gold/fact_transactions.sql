-- Gold: the fact table. Grain: one row per transaction (event_id), from every source.
-- Each customer key is the dimension version that was valid when the event happened (a
-- point-in-time join on the half-open range [valid_from, valid_to)). An ID with no match, which is
-- every PaySim ID, maps to the Unknown member (-1); the raw ID stays on the row.
-- It is a materialized view because the join to a history table is a range join, and a streaming
-- table would miss a dimension change that arrives late.
--
-- Layout: liquid clustering on (date_key, type_key), the two columns dashboards filter by. It is
-- not partitioned: at this size (one file of about 280 MB) a daily partition would be a tiny file
-- (ADR-018).
--
-- Simple rule flags, so they can be measured against the fraud label (gold.rule_effectiveness):
--   flag_balance_drain      a transfer or cash-out that empties the whole origin balance
--   flag_night_high_value   a transfer of 1,000 or more between 01:00 and 04:59
--   flag_burst              5 or more transfers by the same customer within 15 minutes either side
--   flag_source_rule        the source's own rule (PaySim's isFlaggedFraud, via status)
CREATE OR REFRESH MATERIALIZED VIEW workspace.gold.fact_transactions (
    CONSTRAINT event_id_present EXPECT (event_id IS NOT NULL) ON VIOLATION FAIL UPDATE,
    CONSTRAINT date_key_present EXPECT (date_key IS NOT NULL) ON VIOLATION FAIL UPDATE,
    CONSTRAINT type_key_present EXPECT (type_key IS NOT NULL) ON VIOLATION FAIL UPDATE,
    CONSTRAINT origin_customer_key_present EXPECT (origin_customer_key IS NOT NULL)
    ON VIOLATION FAIL UPDATE,
    CONSTRAINT counterparty_key_present EXPECT (counterparty_key IS NOT NULL)
    ON VIOLATION FAIL UPDATE,
    CONSTRAINT amount_not_negative EXPECT (amount >= 0) ON VIOLATION FAIL UPDATE
)
CLUSTER BY (date_key, type_key)
COMMENT 'Transactions fact table, one row per event, keyed to the date, type and customer dims'
AS
WITH base AS (
    SELECT
        t.event_id,
        t.event_ts,
        t.txn_type,
        t.customer_id,
        t.counterparty_id,
        t.amount,
        t.origin_balance_before,
        t.origin_balance_after,
        t.status,
        t.status_source,
        t.decline_reason,
        t.is_fraud,
        t.is_zero_amount,
        t.channel,
        t.source_system,
        hour(t.event_ts) AS hour_of_day,
        CASE
            WHEN t.amount < 10 THEN 1
            WHEN t.amount < 100 THEN 2
            WHEN t.amount < 1000 THEN 3
            WHEN t.amount < 10000 THEN 4
            WHEN t.amount < 100000 THEN 5
            ELSE 6
        END AS amount_band_sort,
        sum(CASE WHEN t.txn_type = 'TRANSFER' THEN 1 ELSE 0 END) OVER (
            PARTITION BY t.customer_id
            ORDER BY t.event_ts
            RANGE BETWEEN INTERVAL 15 MINUTES PRECEDING AND INTERVAL 15 MINUTES FOLLOWING
        ) AS transfers_within_15_min
    FROM workspace.silver.transactions AS t
)

SELECT
    b.event_id,
    b.event_ts,
    cast(date_format(b.event_ts, 'yyyyMMdd') AS INT) AS date_key,
    b.hour_of_day,
    ty.type_key,
    b.customer_id,
    b.counterparty_id,
    b.amount,
    b.amount_band_sort,
    b.origin_balance_before,
    b.origin_balance_after,
    b.status,
    b.status_source,
    b.decline_reason,
    b.is_fraud,
    b.is_zero_amount,
    b.channel,
    b.source_system,
    CASE
        WHEN b.hour_of_day < 6 THEN 'night'
        WHEN b.hour_of_day < 12 THEN 'morning'
        WHEN b.hour_of_day < 18 THEN 'afternoon'
        ELSE 'evening'
    END AS day_part,
    coalesce(oc.customer_key, -1) AS origin_customer_key,
    coalesce(cp.customer_key, -1) AS counterparty_key,
    element_at(
        array(
            '0 to 9.99',
            '10 to 99.99',
            '100 to 999.99',
            '1,000 to 9,999.99',
            '10,000 to 99,999.99',
            '100,000 and over'
        ),
        b.amount_band_sort
    ) AS amount_band,
    b.status = 'DECLINED' AS is_declined,
    b.txn_type IN ('TRANSFER', 'CASH_OUT') AND b.amount > 0 AND b.amount = b.origin_balance_before
        AS flag_balance_drain,
    b.txn_type = 'TRANSFER' AND b.hour_of_day BETWEEN 1 AND 4 AND b.amount >= 1000
        AS flag_night_high_value,
    b.txn_type = 'TRANSFER' AND b.transfers_within_15_min >= 5 AS flag_burst,
    b.status_source = 'derived_paysim_rule' AND b.status = 'DECLINED' AS flag_source_rule
FROM base AS b
LEFT JOIN workspace.gold.dim_type AS ty ON b.txn_type = ty.type_name
LEFT JOIN workspace.gold.dim_customer AS oc
    ON
        b.customer_id = oc.customer_id
        AND b.event_ts >= oc.valid_from
        AND b.event_ts < oc.valid_to
LEFT JOIN workspace.gold.dim_customer AS cp
    ON
        b.counterparty_id = cp.customer_id
        AND b.event_ts >= cp.valid_from
        AND b.event_ts < cp.valid_to
