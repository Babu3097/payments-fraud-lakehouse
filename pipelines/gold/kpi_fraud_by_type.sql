-- Gold KPI: fraud by transaction type, per source.
CREATE OR REFRESH MATERIALIZED VIEW workspace.gold.kpi_fraud_by_type
COMMENT 'Volume, value and fraud by transaction type and source'
AS
SELECT
    ty.type_key,
    ty.type_name,
    f.source_system,
    count(*) AS txn_count,
    sum(f.amount) AS txn_value,
    avg(f.amount) AS avg_amount,
    count_if(f.is_fraud) AS fraud_count,
    sum(CASE WHEN f.is_fraud THEN f.amount ELSE 0 END) AS fraud_value,
    try_divide(count_if(f.is_fraud), count(*)) AS fraud_rate
FROM workspace.gold.fact_transactions AS f
INNER JOIN workspace.gold.dim_type AS ty ON f.type_key = ty.type_key
GROUP BY ty.type_key, ty.type_name, f.source_system
