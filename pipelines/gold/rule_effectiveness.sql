-- Gold: how good is each simple rule, measured against the fraud label?
--   precision_rate  of the rows a rule flags, the share that really are fraud
--   recall_rate     of all the fraud, the share the rule flags
-- A single pass over the fact: each row is expanded into one row per rule. On PaySim, the source's
-- own rule flags 16 of 8,213 fraud rows, while "balance drain" flags almost all of them. That is a
-- simulation artifact (docs/data_profile.md), so read the numbers with that caveat.
CREATE OR REFRESH MATERIALIZED VIEW workspace.gold.rule_effectiveness
COMMENT 'Precision and recall of each rule against the fraud label, per source'
AS
WITH expanded AS (
    SELECT
        f.source_system,
        f.is_fraud,
        explode(
            array(
                named_struct('rule_name', 'source_rule', 'flagged', f.flag_source_rule),
                named_struct('rule_name', 'balance_drain', 'flagged', f.flag_balance_drain),
                named_struct('rule_name', 'night_high_value', 'flagged', f.flag_night_high_value),
                named_struct('rule_name', 'burst', 'flagged', f.flag_burst),
                named_struct(
                    'rule_name', 'any_of_our_rules',
                    'flagged', f.flag_balance_drain OR f.flag_night_high_value OR f.flag_burst
                )
            )
        ) AS r
    FROM workspace.gold.fact_transactions AS f
)

SELECT
    expanded.source_system,
    r.rule_name,
    count_if(r.flagged) AS flagged_count,
    count_if(r.flagged AND expanded.is_fraud) AS fraud_caught,
    count_if(expanded.is_fraud) AS fraud_total,
    try_divide(count_if(r.flagged AND expanded.is_fraud), count_if(r.flagged)) AS precision_rate,
    try_divide(count_if(r.flagged AND expanded.is_fraud), count_if(expanded.is_fraud))
        AS recall_rate
FROM expanded
GROUP BY expanded.source_system, r.rule_name
