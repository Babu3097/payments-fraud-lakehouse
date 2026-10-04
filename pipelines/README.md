# pipelines/

Code that runs **on Databricks**: the Lakeflow Spark Declarative Pipeline definitions
(bronze, silver, gold) and Auto Loader ingestion. Added from Phase 2 onwards.

Anything that can be tested without a cluster belongs in `src/` instead, so unit
tests stay fast and run in CI.
