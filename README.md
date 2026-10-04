# Payments Fraud-Monitoring Lakehouse

![CI](https://github.com/Babu3097/payments-fraud-lakehouse/actions/workflows/ci.yml/badge.svg)

An end-to-end data engineering project on **Databricks**: it ingests payment transactions
incrementally, cleans and validates them, models them as a star schema, and answers fraud
monitoring questions. It uses the public [PaySim](https://www.kaggle.com/datasets/ealaxi/paysim1)
dataset (synthetic mobile-money transactions with fraud labels), a Python generator that
produces new daily transaction files, and a UK bank-holiday reference API.

> **Status: work in progress (phases 0 and 1 of 7 complete: tooling, repo skeleton, CI).**
> This README grows with the project. See the [roadmap](#roadmap).

## Questions it will answer

- Daily transaction volume, value, approval and fraud rates
- Fraud by transaction type, amount band and time of day
- Customers and merchants with unusual activity (rule-based flags)
- Reconciliation: do the totals in gold match the raw source? (the run fails if not)

## Planned architecture

See [docs/architecture.md](docs/architecture.md) for the diagram. In short:
`PaySim CSV + daily generator files + GOV.UK bank holidays API` → Auto Loader → **bronze** →
**silver** (expectations, quarantine) → **gold** (star schema, KPIs) → AI/BI dashboard and
Power BI.

## Tech stack

Databricks Free Edition · Unity Catalog · Delta Lake · Lakeflow Spark Declarative Pipelines ·
Auto Loader · PySpark and SQL · Databricks Asset Bundles · Python 3.12 with `uv` · pytest ·
ruff and sqlfluff with pre-commit · GitHub Actions

## Repository layout

| Folder | Purpose |
|---|---|
| `src/` | Reusable, unit-tested Python (generator, API client, helpers) |
| `pipelines/` | Code that runs on Databricks (Lakeflow pipelines, Auto Loader) |
| `sql/` | Standalone SQL: setup, KPIs, reconciliation checks |
| `resources/` | Asset Bundle resource definitions (jobs, pipelines) |
| `tests/` | pytest unit tests |
| `docs/` | Architecture, data model, setup guide, runbook, design decisions |

## Getting started

Setup steps are in [docs/setup.md](docs/setup.md). Design decisions and their reasons are
logged in [docs/decisions.md](docs/decisions.md).

## Roadmap

- [x] Phase 0: tooling and workspace setup
- [x] Phase 1: repo skeleton, pre-commit, CI
- [ ] Phase 2: bronze (PaySim, generator, API, Auto Loader)
- [ ] Phase 3: silver (typing, dedup, expectations, quarantine)
- [ ] Phase 4: gold (star schema, SCD2, KPIs, reconciliation)
- [ ] Phase 5: automation (scheduled job, alerts, Asset Bundle, idempotency)
- [ ] Phase 6: quality (tests, CI, data quality summary)
- [ ] Phase 7: reporting (Power BI, dashboard, final README)
