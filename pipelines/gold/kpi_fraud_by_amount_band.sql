-- Gold KPI: fraud by amount band, per source. amount_band_sort keeps the bands in order.
CREATE OR REFRESH MATERIALIZED VIEW workspace.gold.kpi_fraud_by_amount_band
COMMENT 'Volume, value and fraud by amount band and source'
AS
SELECT
    f.amount_band_sort,
    f.amount_band,
    f.source_system,
    count(*) AS txn_count,
    sum(f.amount) AS txn_value,
    count_if(f.is_fraud) AS fraud_count,
    sum(CASE WHEN f.is_fraud THEN f.amount ELSE 0 END) AS fraud_value,
    try_divide(count_if(f.is_fraud), count(*)) AS fraud_rate
FROM workspace.gold.fact_transactions AS f
GROUP BY f.amount_band_sort, f.amount_band, f.source_system
