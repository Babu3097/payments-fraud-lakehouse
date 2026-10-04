-- Silver step 1: one schema for every source. Each row's checks are evaluated and recorded in
-- failed_checks; nothing is dropped here. The next two tables split the rows on that list.
-- PRIVATE: a working table inside the pipeline, not published to the catalog.
CREATE OR REFRESH PRIVATE STREAMING TABLE transactions_unified (
    event_id STRING,
    event_ts TIMESTAMP,
    event_date DATE,
    txn_type STRING,
    amount DECIMAL(18, 2),
    customer_id STRING,
    counterparty_id STRING,
    counterparty_kind STRING,
    origin_balance_before DECIMAL(18, 2),
    origin_balance_after DECIMAL(18, 2),
    dest_balance_before DECIMAL(18, 2),
    dest_balance_after DECIMAL(18, 2),
    status STRING,
    status_source STRING,
    decline_reason STRING,
    is_fraud BOOLEAN,
    is_zero_amount BOOLEAN,
    is_bank_holiday BOOLEAN,
    channel STRING,
    source_system STRING,
    event_ts_raw STRING,
    failed_checks ARRAY<STRING>,
    _source_file STRING,
    _ingested_at TIMESTAMP
);

-- Source 1: the daily feed from the generator. The event time arrives as text and the feed
-- contains bad values on purpose, so parsing is where those rows are caught.
CREATE FLOW unify_generated AS INSERT INTO transactions_unified BY NAME
WITH h AS (
    SELECT DISTINCT holiday_date
    FROM workspace.silver.bank_holidays
    WHERE division = 'england-and-wales'
)

SELECT
    t.event_id,
    t.type AS txn_type,
    t.amount,
    t.customer_id,
    t.counterparty_id,
    t.origin_balance_before,
    t.origin_balance_after,
    cast(NULL AS DECIMAL(18, 2)) AS dest_balance_before,
    cast(NULL AS DECIMAL(18, 2)) AS dest_balance_after,
    t.status,
    'source_system' AS status_source,
    t.decline_reason,
    t.channel,
    t.source_system,
    t.event_ts AS event_ts_raw,
    t._source_file,
    t._ingested_at,
    try_to_timestamp(t.event_ts) AS event_ts,
    to_date(try_to_timestamp(t.event_ts)) AS event_date,
    CASE left(t.counterparty_id, 1) WHEN 'M' THEN 'merchant' WHEN 'C' THEN 'customer' END
        AS counterparty_kind,
    t.is_fraud = 1 AS is_fraud,
    t.amount = 0 AS is_zero_amount,
    h.holiday_date IS NOT NULL AS is_bank_holiday,
    filter(
        array(
            CASE
                WHEN
                    t.customer_id IS NULL
                    OR t.amount IS NULL
                    OR t.type IS NULL
                    OR t.event_ts IS NULL
                    THEN 'NULL_REQUIRED_FIELD'
            END,
            CASE WHEN t.amount < 0 THEN 'NEGATIVE_AMOUNT' END,
            CASE
                WHEN
                    t.type IS NOT NULL
                    AND t.type NOT IN ('CASH_OUT', 'PAYMENT', 'CASH_IN', 'TRANSFER', 'DEBIT')
                    THEN 'UNKNOWN_TYPE'
            END,
            CASE
                WHEN
                    t.event_ts IS NOT NULL AND try_to_timestamp(t.event_ts) IS NULL
                    THEN 'BAD_TIMESTAMP'
            END
        ),
        x -> x IS NOT NULL
    ) AS failed_checks
FROM STREAM (workspace.bronze.transactions_daily) AS t
-- One row per date, so a date with two holiday names can never duplicate a transaction.
LEFT JOIN h
    ON h.holiday_date = to_date(try_to_timestamp(t.event_ts));

-- Source 2: the PaySim history. PaySim has no id, no timestamp and no status, so they are derived:
-- the id is a hash of every column (the file has no duplicate rows, see docs/data_profile.md),
-- the time is the step counted in hours from the anchor in ADR-008, and the status follows ADR-005.
-- A merchant recipient's balances are 0 in the file, meaning unknown, so they become NULL.
CREATE FLOW unify_paysim AS INSERT INTO transactions_unified BY NAME
WITH p AS (
    SELECT
        *,
        timestampadd(HOUR, step - 1, TIMESTAMP '2026-08-20 00:00:00') AS ts
    FROM STREAM (workspace.bronze.paysim_transactions)
),

h AS (
    SELECT DISTINCT holiday_date
    FROM workspace.silver.bank_holidays
    WHERE division = 'england-and-wales'
)

SELECT
    p.ts AS event_ts,
    p.type AS txn_type,
    p.amount,
    p.nameorig AS customer_id,
    p.namedest AS counterparty_id,
    p.oldbalanceorg AS origin_balance_before,
    p.newbalanceorig AS origin_balance_after,
    'derived_paysim_rule' AS status_source,
    cast(NULL AS STRING) AS channel,
    'paysim' AS source_system,
    cast(NULL AS STRING) AS event_ts_raw,
    p._source_file,
    p._ingested_at,
    concat(
        'P',
        substring(
            sha2(
                concat_ws(
                    '|', p.step, p.type, p.amount, p.nameorig, p.oldbalanceorg, p.newbalanceorig,
                    p.namedest, p.oldbalancedest, p.newbalancedest, p.isfraud, p.isflaggedfraud
                ),
                256
            ),
            1,
            24
        )
    ) AS event_id,
    to_date(p.ts) AS event_date,
    CASE left(p.namedest, 1) WHEN 'M' THEN 'merchant' WHEN 'C' THEN 'customer' END
        AS counterparty_kind,
    CASE WHEN left(p.namedest, 1) = 'M' THEN NULL ELSE p.oldbalancedest END AS dest_balance_before,
    CASE WHEN left(p.namedest, 1) = 'M' THEN NULL ELSE p.newbalancedest END AS dest_balance_after,
    CASE WHEN p.isflaggedfraud = 1 THEN 'DECLINED' ELSE 'APPROVED' END AS status,
    CASE WHEN p.isflaggedfraud = 1 THEN 'PAYSIM_FLAGGED' END AS decline_reason,
    p.isfraud = 1 AS is_fraud,
    p.amount = 0 AS is_zero_amount,
    h.holiday_date IS NOT NULL AS is_bank_holiday,
    filter(
        array(
            CASE
                WHEN p.nameorig IS NULL OR p.amount IS NULL OR p.type IS NULL OR p.step IS NULL
                    THEN 'NULL_REQUIRED_FIELD'
            END,
            CASE WHEN p.amount < 0 THEN 'NEGATIVE_AMOUNT' END,
            CASE
                WHEN
                    p.type IS NOT NULL
                    AND p.type NOT IN ('CASH_OUT', 'PAYMENT', 'CASH_IN', 'TRANSFER', 'DEBIT')
                    THEN 'UNKNOWN_TYPE'
            END
        ),
        x -> x IS NOT NULL
    ) AS failed_checks
FROM p
LEFT JOIN h
    ON h.holiday_date = to_date(p.ts);
