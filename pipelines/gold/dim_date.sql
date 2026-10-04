-- Gold: one row per calendar day, 2026 to 2028, with the bank holidays of the three UK regions.
-- 2028 is as far as the holiday API goes. The range must outlast the daily job, or the day it ends
-- every new transaction has no date row and the reconciliation fails the run.
-- Holiday data comes from silver.bank_holidays (parsed from the GOV.UK payload). England and Wales
-- is the headline flag; Scotland and Northern Ireland are kept so a customer's region can pick the
-- right calendar later (ADR-015). date_key is the usual yyyymmdd integer.
CREATE OR REFRESH MATERIALIZED VIEW workspace.gold.dim_date (
    CONSTRAINT date_key_present EXPECT (date_key IS NOT NULL) ON VIOLATION FAIL UPDATE,
    CONSTRAINT date_key_matches_date EXPECT (
        date_key = CAST(DATE_FORMAT(calendar_date, 'yyyyMMdd') AS INT)
    ) ON VIOLATION FAIL UPDATE
)
COMMENT 'Calendar dimension with bank-holiday flags for the three UK regions'
AS
WITH holidays AS (
    SELECT
        holiday_date,
        MAX(CASE WHEN division = 'england-and-wales' THEN title END) AS eaw_name,
        MAX(CASE WHEN division = 'scotland' THEN title END) AS scotland_name,
        MAX(CASE WHEN division = 'northern-ireland' THEN title END) AS ni_name
    FROM workspace.silver.bank_holidays
    GROUP BY holiday_date
),

calendar AS (
    SELECT EXPLODE(SEQUENCE(DATE '2026-01-01', DATE '2028-12-31', INTERVAL 1 DAY)) AS calendar_date
)

SELECT
    CAST(DATE_FORMAT(c.calendar_date, 'yyyyMMdd') AS INT) AS date_key,
    c.calendar_date,
    h.eaw_name AS bank_holiday_name_eaw,
    YEAR(c.calendar_date) AS calendar_year,
    MONTH(c.calendar_date) AS calendar_month,
    DATE_FORMAT(c.calendar_date, 'MMMM') AS month_name,
    DAY(c.calendar_date) AS day_of_month,
    DATE_FORMAT(c.calendar_date, 'EEEE') AS day_name,
    WEEKOFYEAR(c.calendar_date) AS iso_week,
    DAYOFWEEK(c.calendar_date) IN (1, 7) AS is_weekend,
    h.eaw_name IS NOT NULL AS is_bank_holiday_eaw,
    h.scotland_name IS NOT NULL AS is_bank_holiday_scotland,
    h.ni_name IS NOT NULL AS is_bank_holiday_ni
FROM calendar AS c
LEFT JOIN holidays AS h ON c.calendar_date = h.holiday_date
