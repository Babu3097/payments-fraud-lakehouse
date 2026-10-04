-- Gold: customers and merchants with unusual activity on a given day, by simple documented rules.
-- Thresholds (change them here, then rerun the pipeline):
--   HIGH_FREQUENCY   a sender with 8 or more transactions in one day
--   BURST            a sender with any transaction that is part of a burst (fact flag_burst)
--   BALANCE_DRAIN    a sender with any transaction that empties the whole origin balance
--   HUB_RECIPIENT    a recipient of 5 or more transfers from 5 or more different senders in a day
-- Only flagged entity-days are kept. These are leads for an analyst, not verdicts.
CREATE OR REFRESH MATERIALIZED VIEW workspace.gold.unusual_activity (
    CONSTRAINT has_reason EXPECT (size(flag_reasons) > 0) ON VIOLATION FAIL UPDATE
)
COMMENT 'Entity-days flagged by simple rules: high frequency, burst, balance drain, hub recipient'
AS
WITH sender_days AS (
    SELECT
        f.date_key,
        f.customer_id AS entity_id,
        count(*) AS txn_count,
        sum(f.amount) AS total_amount,
        count_if(f.is_fraud) AS fraud_count,
        bool_or(f.flag_burst) AS has_burst,
        bool_or(f.flag_balance_drain) AS has_drain
    FROM workspace.gold.fact_transactions AS f
    GROUP BY f.date_key, f.customer_id
),

recipient_days AS (
    SELECT
        f.date_key,
        f.counterparty_id AS entity_id,
        count(*) AS txn_count,
        sum(f.amount) AS total_amount,
        count_if(f.is_fraud) AS fraud_count,
        count(DISTINCT f.customer_id) AS distinct_senders
    FROM workspace.gold.fact_transactions AS f
    INNER JOIN workspace.gold.dim_type AS ty ON f.type_key = ty.type_key
    WHERE ty.type_name = 'TRANSFER'
    GROUP BY f.date_key, f.counterparty_id
),

flagged AS (
    SELECT
        date_key,
        entity_id,
        'sender' AS entity_role,
        txn_count,
        total_amount,
        fraud_count,
        filter(
            array(
                CASE WHEN txn_count >= 8 THEN 'HIGH_FREQUENCY' END,
                CASE WHEN has_burst THEN 'BURST' END,
                CASE WHEN has_drain THEN 'BALANCE_DRAIN' END
            ),
            x -> x IS NOT NULL
        ) AS flag_reasons
    FROM sender_days

    UNION ALL

    SELECT
        date_key,
        entity_id,
        'recipient' AS entity_role,
        txn_count,
        total_amount,
        fraud_count,
        filter(
            array(CASE WHEN txn_count >= 5 AND distinct_senders >= 5 THEN 'HUB_RECIPIENT' END),
            x -> x IS NOT NULL
        ) AS flag_reasons
    FROM recipient_days
)

SELECT
    date_key,
    entity_id,
    entity_role,
    txn_count,
    total_amount,
    fraud_count,
    flag_reasons
FROM flagged
WHERE size(flag_reasons) > 0
