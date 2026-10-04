-- Gold KPI: daily volume, value, approval rate and fraud rate, per source.
-- Source is kept apart on purpose: PaySim's approval rate is derived from its own flag (ADR-005)
-- and is almost constant, so mixing it with the generator's real declines would mislead.
-- Definitions are in docs/kpis.md. "Missed" fraud is fraud that was approved.
CREATE OR REFRESH MATERIALIZED VIEW workspace.gold.kpi_daily (
    CONSTRAINT approval_rate_valid EXPECT (approval_rate BETWEEN 0 AND 1),
    CONSTRAINT fraud_rate_valid EXPECT (fraud_rate BETWEEN 0 AND 1)
)
COMMENT 'Daily transaction volume, value, approval rate and fraud rate by source'
AS
SELECT
    f.date_key,
    d.calendar_date,
    d.is_bank_holiday_eaw,
    f.source_system,
    count(*) AS txn_count,
    sum(f.amount) AS txn_value,
    count_if(NOT f.is_declined) AS approved_count,
    sum(CASE WHEN f.is_declined THEN 0 ELSE f.amount END) AS approved_value,
    count_if(f.is_declined) AS declined_count,
    try_divide(count_if(NOT f.is_declined), count(*)) AS approval_rate,
    count_if(f.is_fraud) AS fraud_count,
    sum(CASE WHEN f.is_fraud THEN f.amount ELSE 0 END) AS fraud_value,
    try_divide(count_if(f.is_fraud), count(*)) AS fraud_rate,
    count_if(f.is_fraud AND NOT f.is_declined) AS fraud_missed_count,
    sum(CASE WHEN f.is_fraud AND NOT f.is_declined THEN f.amount ELSE 0 END) AS fraud_missed_value,
    try_divide(count_if(f.is_fraud AND f.is_declined), count_if(f.is_fraud)) AS fraud_catch_rate
FROM workspace.gold.fact_transactions AS f
INNER JOIN workspace.gold.dim_date AS d ON f.date_key = d.date_key
GROUP BY f.date_key, d.calendar_date, d.is_bank_holiday_eaw, f.source_system
