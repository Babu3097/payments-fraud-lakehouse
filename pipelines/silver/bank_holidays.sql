-- Silver: the GOV.UK bank holidays payload parsed into one row per region and date.
-- Bronze stores the payload exactly as received; parsing here means an API change cannot break
-- ingestion. If several payloads have landed, the most recent row for each holiday wins.
CREATE OR REFRESH MATERIALIZED VIEW workspace.silver.bank_holidays (
    CONSTRAINT holiday_date_present EXPECT (holiday_date IS NOT NULL) ON VIOLATION FAIL UPDATE,
    CONSTRAINT known_division EXPECT (
        division IN ('england-and-wales', 'scotland', 'northern-ireland')
    ) ON VIOLATION FAIL UPDATE,
    CONSTRAINT title_present EXPECT (title IS NOT NULL AND length(title) > 0)
)
COMMENT 'Bank holidays by region and date, parsed from the GOV.UK payload'
AS
WITH parsed AS (
    SELECT
        _ingested_at,
        from_json(
            payload,
            'MAP<STRING, STRUCT<division: STRING, events: ARRAY<STRUCT<title: STRING, date: STRING, notes: STRING, bunting: BOOLEAN>>>>' -- noqa: LT05
        ) AS calendar
    FROM workspace.bronze.bank_holidays_raw
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
)

SELECT
    events.division,
    event.title,
    event.notes,
    event.bunting,
    events._ingested_at AS loaded_at,
    to_date(event.date) AS holiday_date
FROM events
QUALIFY row_number() OVER (
    PARTITION BY events.division, to_date(event.date), event.title
    ORDER BY events._ingested_at DESC
) = 1
