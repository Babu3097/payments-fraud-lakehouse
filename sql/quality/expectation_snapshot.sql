-- Expectation results of the latest pipeline update, from the pipeline event log. Databricks only
-- (event_log() does not exist in open-source Spark). {pipeline_id} is filled in by run-checks.
-- Metrics are summed per update, the pattern the Databricks documentation uses.
WITH latest_update AS (
    SELECT log.origin.update_id AS latest_update_id
    FROM event_log('{pipeline_id}') AS log
    WHERE log.event_type = 'create_update'
    ORDER BY log.timestamp DESC
    LIMIT 1
),

expectations AS (
    SELECT
        log.origin.update_id AS source_update_id,
        explode(
            from_json(
                log.details:flow_progress.data_quality.expectations,
                'ARRAY<STRUCT<name: STRING, dataset: STRING, passed_records: BIGINT, failed_records: BIGINT>>' -- noqa: LT05
            )
        ) AS item
    FROM event_log('{pipeline_id}') AS log
    WHERE
        log.event_type = 'flow_progress'
        AND log.details:flow_progress.data_quality.expectations IS NOT NULL
),

flattened AS (
    SELECT
        expectations.source_update_id AS update_id,
        expectations.item.dataset AS dataset_name,
        expectations.item.name AS expectation_name,
        expectations.item.passed_records AS passed,
        expectations.item.failed_records AS failed
    FROM expectations
)

SELECT
    flattened.update_id,
    flattened.dataset_name AS dataset,
    flattened.expectation_name AS expectation,
    sum(flattened.passed) AS passed_records,
    sum(flattened.failed) AS failed_records
FROM flattened
INNER JOIN latest_update ON flattened.update_id = latest_update.latest_update_id
GROUP BY flattened.update_id, flattened.dataset_name, flattened.expectation_name
