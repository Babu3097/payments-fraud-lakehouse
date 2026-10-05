# sql/

Standalone SQL: schema setup, gold KPI queries, reconciliation checks, and dashboard
queries. Linted with sqlfluff (Databricks dialect) on every commit and in CI.

`checks/` holds the reconciliation suites the `verify` task runs. `quality/` holds the setup for
`workspace.quality`, the history of those results (docs/data_quality.md).
