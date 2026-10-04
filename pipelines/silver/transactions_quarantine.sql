-- Silver step 2, other side: every row that failed a check, kept with the reason and the original
-- values. Nothing is silently dropped, so data quality is observable and the rows can be repaired
-- and replayed. event_ts_raw holds the original text, because a bad timestamp parses to NULL.
CREATE OR REFRESH STREAMING TABLE workspace.silver.transactions_quarantine (
    CONSTRAINT has_reason EXPECT (size(failed_checks) > 0) ON VIOLATION FAIL UPDATE,
    CONSTRAINT has_event_id EXPECT (event_id IS NOT NULL),
    CONSTRAINT has_raw_record EXPECT (raw_record IS NOT NULL)
)
COMMENT 'Rejected transactions with the reasons they failed, never dropped silently'
AS
SELECT
    event_id,
    failed_checks,
    source_system,
    _source_file,
    _ingested_at,
    current_timestamp() AS quarantined_at,
    to_json(
        struct(
            event_id,
            event_ts_raw,
            txn_type,
            amount,
            customer_id,
            counterparty_id,
            origin_balance_before,
            origin_balance_after,
            status,
            decline_reason,
            is_fraud,
            channel
        )
    ) AS raw_record
FROM STREAM (transactions_unified)
WHERE size(failed_checks) > 0;
