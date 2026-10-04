-- Gold: the five transaction types. A small hand-written reference table.
-- can_be_fraud records what we observed in the data (docs/data_profile.md): in PaySim, fraud only
-- occurs on TRANSFER and CASH_OUT, and the generator injects fraud on the same two types.
CREATE OR REFRESH MATERIALIZED VIEW workspace.gold.dim_type (
    CONSTRAINT type_key_present EXPECT (type_key IS NOT NULL) ON VIOLATION FAIL UPDATE,
    CONSTRAINT type_name_unique_looking EXPECT (type_name IS NOT NULL AND length(type_name) > 0)
)
COMMENT 'Transaction type dimension: one row per type'
AS
SELECT
    type_key,
    type_name,
    category,
    description,
    can_be_fraud
FROM
    VALUES
    (1, 'CASH_IN', 'cash', 'Cash paid into an account', FALSE),
    (2, 'CASH_OUT', 'cash', 'Cash withdrawn from an account', TRUE),
    (3, 'PAYMENT', 'payment', 'Payment to a merchant', FALSE),
    (4, 'TRANSFER', 'transfer', 'Transfer between accounts', TRUE),
    (5, 'DEBIT', 'payment', 'Debit from an account', FALSE)
        AS t (type_key, type_name, category, description, can_be_fraud)
