-- Silver step 2: the clean transactions, one row per event_id.
-- Only rows with no failed checks get here. Deduplication is an Auto CDC upsert (SCD Type 1)
-- keyed on event_id: a replayed or duplicated event updates the same row instead of adding one,
-- so it is idempotent and needs no unbounded streaming state (ADR-013).
-- Deliberate SELECT *: every column of the working table passes through to the CDC flow.
CREATE TEMPORARY VIEW transactions_valid AS
SELECT * -- noqa: AM04
FROM STREAM (transactions_unified)
WHERE size(failed_checks) = 0;

-- The expectations are a safety net on top of the split: if the split logic ever let a bad row
-- through, the hard rules fail the update instead of letting it reach gold.
CREATE OR REFRESH STREAMING TABLE workspace.silver.transactions (
    CONSTRAINT event_id_present EXPECT (event_id IS NOT NULL) ON VIOLATION FAIL UPDATE,
    CONSTRAINT event_ts_present EXPECT (event_ts IS NOT NULL) ON VIOLATION FAIL UPDATE,
    CONSTRAINT customer_present EXPECT (customer_id IS NOT NULL) ON VIOLATION FAIL UPDATE,
    CONSTRAINT amount_not_negative EXPECT (amount IS NOT NULL AND amount >= 0)
    ON VIOLATION FAIL UPDATE,
    CONSTRAINT known_type EXPECT (
        txn_type IN ('CASH_OUT', 'PAYMENT', 'CASH_IN', 'TRANSFER', 'DEBIT')
    ) ON VIOLATION FAIL UPDATE,
    CONSTRAINT known_status EXPECT (status IN ('APPROVED', 'DECLINED')) ON VIOLATION FAIL UPDATE,
    CONSTRAINT decline_has_reason EXPECT (status = 'APPROVED' OR decline_reason IS NOT NULL),
    CONSTRAINT counterparty_present EXPECT (counterparty_id IS NOT NULL),
    CONSTRAINT event_date_in_range EXPECT (
        event_date BETWEEN DATE '2026-08-20' AND DATE '2028-12-31'
    )
)
COMMENT 'Clean, deduplicated transactions from every source, one row per event_id';

CREATE FLOW deduplicate_transactions AS AUTO CDC INTO workspace.silver.transactions
FROM STREAM (transactions_valid)
KEYS (event_id)
SEQUENCE BY _ingested_at
COLUMNS * EXCEPT (failed_checks, event_ts_raw)
STORED AS SCD TYPE 1;
