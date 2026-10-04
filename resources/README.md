# resources/

Databricks Asset Bundle resource definitions as YAML, so the whole project deploys as code.
The bundle root file is `../databricks.yml`; every `*.yml` here is included by it.

| File | What it defines |
|---|---|
| `payments_pipeline.pipeline.yml` | The one Lakeflow pipeline that builds bronze, silver and gold (ADR-009) |
| `daily.job.yml` | The daily job: generate, pull holidays, refresh the pipeline, verify (ADR-021, ADR-022) |

File names follow `<name>.<resource type>.yml`. See `docs/setup.md` to deploy.
