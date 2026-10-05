-- Silver: the GOV.UK bank holidays payload parsed into one row per region and date.
-- Bronze stores the payload exactly as received; parsing here means an API change cannot break
-- ingestion. Each payload is the whole calendar, so the newest payload is the truth: a holiday that
-- is missing from it is gone (a holiday that was moved is no longer flagged on its old date).
CREATE OR REFRESH MATERIALIZED VIEW workspace.silver.bank_holidays (
    CONSTRAINT holiday_date_present EXPECT (holiday_date IS NOT NULL) ON VIOLATION FAIL UPDATE,
    CONSTRAINT known_division EXPECT (
        division IN ('england-and-wales', 'scotland', 'northern-ireland')
    ) ON VIOLATION FAIL UPDATE,
    CONSTRAINT title_present EXPECT (title IS NOT NULL AND length(title) > 0)
)
COMMENT 'Bank holidays by region and date, parsed from the GOV.UK payload'
AS
WITH latest AS (
    SELECT max(_ingested_at) AS latest_at
    FROM workspace.bronze.bank_holidays_raw
),

parsed AS (
    SELECT
        landed._ingested_at,
        from_json(
            landed.payload,
            'MAP<STRING, STRUCT<division: STRING, events: ARRAY<STRUCT<title: STRING, date: STRING, notes: STRING, bunting: BOOLEAN>>>>' -- noqa: LT05
        ) AS calendar
    FROM workspace.bronze.bank_holidays_raw AS landed
    INNER JOIN latest ON landed._ingested_at = latest.latest_at
),

regions AS (
    SELECT
        _ingested_at,
        explode(calendar) AS (division, region)
    FROM parsed
),

events AS (
    SELECT
        regions.division,
        regions._ingested_at,
        explode(region.events) AS event
    FROM regions
),

-- One row per holiday even if a payload lists it twice. Plain row_number and a filter, not
-- QUALIFY, so the same SQL runs in open-source Spark as well as on Databricks (the unit tests
-- rely on that).
ranked AS (
    SELECT
        events.division,
        event.title,
        event.notes,
        event.bunting,
        events._ingested_at AS loaded_at,
        to_date(event.date) AS holiday_date,
        row_number() OVER (
            PARTITION BY events.division, to_date(event.date), event.title
            ORDER BY events._ingested_at DESC
        ) AS recency_rank
    FROM events
)

SELECT
    division,
    title,
    notes,
    bunting,
    loaded_at,
    holiday_date
FROM ranked
WHERE recency_rank = 1
