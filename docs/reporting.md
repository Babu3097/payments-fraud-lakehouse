# Reporting: the AI/BI dashboard and Power BI

Two front ends read the same gold tables, so they must agree. Decisions: ADR-028.

| | AI/BI dashboard | Power BI report |
|---|---|---|
| Lives | In the workspace, deployed by the bundle | In Power BI Desktop (a `.pbix` you build from this guide) |
| Reads | The four KPI views | The star schema (fact plus three dimensions) |
| Best for | Monitoring: one page, always current | Exploring: slicing by customer segment and region |
| In the repo as | `dashboards/build_dashboard.py` and the JSON it writes | This guide (a `.pbix` is binary, so it is not committed) |

## The AI/BI dashboard

One page, "Fraud monitoring".

| Panel | Source view | Notes |
|---|---|---|
| Four KPI tiles: transactions, fraud rate, approval rate, fraud approved (missed) value | `kpi_daily` | Follow the date and source filters |
| Daily transactions and daily fraud rate | `kpi_daily` | Follow both filters |
| Fraud rate by transaction type, and by hour of day | `kpi_fraud_by_type`, `kpi_fraud_by_hour` | All-time: these views have no date column |
| Rule effectiveness table (precision and recall) | `rule_effectiveness` | All-time |

The **source filter defaults to the generated feed** (`generator-v1`). PaySim's daily trend and its
overnight fraud peak are simulation artifacts (docs/kpis.md, caveats 1 and 4), so the default view
should not suggest a trend that is not there. Switch the filter to see PaySim.

Ratios are defined once as dataset measures (`SUM(fraud_count) / SUM(txn_count)`), so a tile, a
chart and a filtered view always agree and the rate stays correct when the filter changes.
Averaging the per-row `fraud_rate` column would be wrong.

### Change it and deploy it

```bash
uv run python dashboards/build_dashboard.py     # rewrites the JSON
uv run pytest tests/test_dashboard.py            # fields exist, grid has no gaps, versions are valid
databricks bundle deploy -t dev                  # deploys the dashboard along with the rest
```

The queries use bare table names. The bundle supplies the catalog and `gold` as the schema, and the
warehouse is looked up by name (`Serverless Starter Warehouse`), so no id or address is committed.
Dataset queries were run on the warehouse before the first deploy: the measures gave 0.30% fraud
rate and 96.9% approval for the generated feed, the same as `docs/kpis.md`.

## Power BI

Power BI Desktop only runs on Windows, so this was written as a guide and not verified in Power BI
itself in this project. The numbers below can be checked against the dashboard.

### Connect

1. Get Data, then **Databricks**. Server hostname and HTTP path are on the SQL warehouse's
   Connection details tab. Sign in with your own account (Microsoft Entra ID or a personal access
   token). Never put a token in a file that is committed.
2. Choose catalog `workspace`, schema `gold`, and select `fact_transactions`, `dim_customer`,
   `dim_date`, `dim_type`.
3. **Storage mode.** Import `dim_date`, `dim_type` and `dim_customer`. For the fact (about 7 million
   rows) Import is faster to use and works on the free warehouse, which sleeps when idle and would make
   DirectQuery slow to wake. Remove columns you do not report on (the balances) before loading.
   Refresh manually in Desktop after each daily run.

### Model

Mark `dim_date` as the date table on `calendar_date`. It is contiguous (every day to 2028), which
time intelligence needs.

| From (many) | To (one) | Active |
|---|---|---|
| `fact_transactions[date_key]` | `dim_date[date_key]` | yes |
| `fact_transactions[type_key]` | `dim_type[type_key]` | yes |
| `fact_transactions[origin_customer_key]` | `dim_customer[customer_key]` | yes (the sender) |
| `fact_transactions[counterparty_key]` | `dim_customer[customer_key]` | **no** (the recipient) |

`dim_customer` is a role-playing dimension: sender and recipient both point at it, so one relationship
is active and the other is switched on inside a measure with `USERELATIONSHIP`. Because the
dimension is SCD2, the fact already points at the customer version that was valid at event time, so
a segment change never rewrites history. Keep `is_current` as a slicer only when you want "today's"
segment, because the relationship itself already gives the point-in-time one.
Rows for PaySim customers join to the Unknown member (`customer_key = -1`), so segment and region
show "Unknown" for PaySim.

### Measures

```dax
Transactions = COUNTROWS ( fact_transactions )

Fraud Count =
CALCULATE ( [Transactions], fact_transactions[is_fraud] = TRUE () )

Fraud Rate = DIVIDE ( [Fraud Count], [Transactions] )

Approval Rate =
DIVIDE (
    CALCULATE ( [Transactions], fact_transactions[is_declined] = FALSE () ),
    [Transactions]
)

Fraud Missed Value =
CALCULATE (
    SUM ( fact_transactions[amount] ),
    fact_transactions[is_fraud] = TRUE (),
    fact_transactions[is_declined] = FALSE ()
)

Fraud Catch Rate =
DIVIDE (
    CALCULATE ( [Fraud Count], fact_transactions[is_declined] = TRUE () ),
    [Fraud Count]
)

Fraud Rate Previous Day =
CALCULATE ( [Fraud Rate], DATEADD ( dim_date[calendar_date], -1, DAY ) )

Received Transactions =
CALCULATE (
    [Transactions],
    USERELATIONSHIP ( fact_transactions[counterparty_key], dim_customer[customer_key] )
)

Balance Drain Precision =
DIVIDE (
    CALCULATE ( [Fraud Count], fact_transactions[flag_balance_drain] = TRUE () ),
    CALCULATE ( [Transactions], fact_transactions[flag_balance_drain] = TRUE () )
)
```

Put a `source_system` slicer on every page and default it to `generator-v1`, for the same reason
as in the dashboard. Compare `Transactions`, `Fraud Rate` and `Approval Rate` with the dashboard
tiles for the same source and dates: they must match, and if they do not, the model is wrong.

### Suggested pages

1. **Overview:** four cards (Transactions, Fraud Rate, Approval Rate, Fraud Missed Value), a line chart
   of Fraud Rate by `dim_date[calendar_date]`, with `Fraud Rate Previous Day` as a second line.
2. **Where:** fraud rate by `dim_type[type_name]`, by `fact_transactions[day_part]`, and by
   `dim_customer[segment]` and `[region]` (the reason to use Power BI: the KPI views have no customer
   attributes).
3. **Rules:** a table of rule precision and the share of fraud approved, with `Fraud Catch Rate`.
