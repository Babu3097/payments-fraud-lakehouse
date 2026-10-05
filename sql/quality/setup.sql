-- The data quality summary (ADR-027). Idempotent: the verify task runs this file on every run, so
-- there is no manual setup step and a rerun never changes what is already there.
-- Statements are separated by semicolons; keep ';' out of comments and strings.
CREATE SCHEMA IF NOT EXISTS workspace.quality
COMMENT 'History of data quality checks and expectation metrics, and the views that summarise it';

-- One row per check per verify run: the append-only history that the scheduled job otherwise
-- throws away. expected and actual are text, because checks compare counts, sums and flags.
CREATE TABLE IF NOT EXISTS workspace.quality.check_results (
    run_at TIMESTAMP NOT NULL,
    run_id STRING,
    suite STRING NOT NULL,
    check_name STRING NOT NULL,
    expected STRING,
    actual STRING,
    passed BOOLEAN NOT NULL
);

-- One row per pipeline expectation per verify run: how many rows passed and failed it in the
-- latest pipeline update (read from the pipeline event log).
CREATE TABLE IF NOT EXISTS workspace.quality.expectation_results (
    run_at TIMESTAMP NOT NULL,
    run_id STRING,
    update_id STRING,
    dataset STRING NOT NULL,
    expectation STRING NOT NULL,
    passed_records BIGINT,
    failed_records BIGINT
);

-- The newest verify run, one row per suite.
CREATE OR REPLACE VIEW workspace.quality.check_scorecard AS
WITH latest AS (
    SELECT max(run_at) AS run_at
    FROM workspace.quality.check_results
)

SELECT
    results.suite,
    latest.run_at,
    count(*) AS checks,
    count_if(results.passed) AS passed,
    count_if(NOT results.passed) AS failed
FROM workspace.quality.check_results AS results
INNER JOIN latest ON results.run_at = latest.run_at
GROUP BY results.suite, latest.run_at;

-- Trend: every verify run, so a check that starts failing shows up on the day it did.
CREATE OR REPLACE VIEW workspace.quality.check_history AS
SELECT
    run_at,
    count(*) AS checks,
    count_if(passed) AS passed,
    count_if(NOT passed) AS failed
FROM workspace.quality.check_results
GROUP BY run_at;

-- The newest expectation snapshot, with the share of rows that failed each rule.
CREATE OR REPLACE VIEW workspace.quality.expectation_scorecard AS
WITH latest AS (
    SELECT max(run_at) AS run_at
    FROM workspace.quality.expectation_results
)

SELECT
    results.dataset,
    results.expectation,
    results.passed_records,
    results.failed_records,
    latest.run_at,
    results.failed_records
    / nullif(results.passed_records + results.failed_records, 0) AS failure_rate
FROM workspace.quality.expectation_results AS results
INNER JOIN latest ON results.run_at = latest.run_at;
