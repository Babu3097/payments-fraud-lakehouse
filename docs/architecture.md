# Architecture

> **Status: planned design.** Updated at the end of each phase. Nothing below is built until
> the README roadmap says so.

## Data flow

```mermaid
flowchart LR
    subgraph Sources
        A["PaySim CSV<br/>(historical batch)"]
        B["Python generator<br/>(new daily transaction files)"]
        C["GOV.UK bank holidays API<br/>(JSON reference data)"]
    end

    subgraph DBX["Databricks - Unity Catalog (catalog: workspace)"]
        V[("UC Volume<br/>landing zone")]
        BR["bronze<br/>raw, append-only"]
        SI["silver<br/>typed, deduplicated,<br/>expectations"]
        QU["quarantine table<br/>rejected rows"]
        GO["gold<br/>star schema + KPIs"]
        REC{{"Reconciliation check<br/>fails the run on mismatch"}}
    end

    A --> V
    B --> V
    C -->|"pulled by a job,<br/>saved as JSON"| V
    V -->|Auto Loader| BR
    BR --> SI
    SI -->|bad rows| QU
    SI --> GO
    GO --> REC
    GO --> WH["SQL warehouse"]
    WH --> PBI["Power BI report"]
    WH --> DASH["AI/BI dashboard"]
```

Operations around the flow:

- **Orchestration:** a scheduled Databricks Job runs the pipeline. It is deployed as code with
  a Databricks Asset Bundle, and a failure sends an email alert.
- **CI:** GitHub Actions runs lint (ruff, sqlfluff) and pytest on every push.

## Layers and what each one guarantees

| Layer | Guarantee | Typical operations |
|---|---|---|
| **Landing (volume)** | Files exactly as received. Never modified. | Drop files from each source |
| **Bronze** | Raw data as Delta tables, append-only, plus audit columns (source file, ingest time). Replayable. | Auto Loader ingestion, no business logic |
| **Silver** | Typed, deduplicated, validated. Every row passed its expectations, or sits in quarantine with the reason. | Cleaning, joins to reference data, expectations |
| **Gold** | Business-ready star schema and KPI tables, reconciled to the raw source. | Dimensional modelling, aggregates |

## Principles the design follows

1. **Incremental:** only new files are processed each run (Auto Loader checkpoints).
2. **Idempotent:** rerunning a job never creates duplicates or changes results.
3. **Quality gates:** bad rows are quarantined, and a reconciliation mismatch fails the run.
4. **Everything as code:** pipelines, jobs and config live in Git and deploy via a bundle.

See [decisions.md](decisions.md) for the reasoning behind each choice.
