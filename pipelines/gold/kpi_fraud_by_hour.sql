-- Gold KPI: fraud by hour of the day, per source.
-- Caveat (docs/data_profile.md): PaySim's fraud is spread evenly across the day while legitimate
-- traffic is quiet overnight, so the overnight fraud RATE is high partly because of the simulation.
CREATE OR REFRESH MATERIALIZED VIEW workspace.gold.kpi_fraud_by_hour
COMMENT 'Volume, value and fraud by hour of day and source'
AS
SELECT
    f.hour_of_day,
    f.day_part,
    f.source_system,
    count(*) AS txn_count,
    sum(f.amount) AS txn_value,
    count_if(f.is_fraud) AS fraud_count,
    sum(CASE WHEN f.is_fraud THEN f.amount ELSE 0 END) AS fraud_value,
    try_divide(count_if(f.is_fraud), count(*)) AS fraud_rate
FROM workspace.gold.fact_transactions AS f
GROUP BY f.hour_of_day, f.day_part, f.source_system
