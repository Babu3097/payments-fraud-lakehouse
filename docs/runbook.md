# Runbook: what to do when it fails

> **Status: skeleton.** Each scenario is filled in with real commands, queries and screenshots
> in the phase that builds the feature. A runbook written before the system exists would be
> guesswork, so unwritten sections are marked TBD.

## How you find out

A failed job run sends an email alert (configured in Phase 5). Start in the Databricks
**Jobs and Pipelines** UI, open the failed run, and find the first failed task.

## Scenarios

Each entry follows the same shape: **Symptom, likely causes, how to check, fix, prevention.**

| Scenario | Written in |
|---|---|
| Reconciliation check failed (gold totals differ from raw) | Phase 4 |
| Expectation failures spiked or the quarantine table grew unexpectedly | Phase 3 |
| Files were uploaded but no new rows appeared | Below (Phase 2) |
| The update was cancelled after a new column appeared | Below (Phase 2) |
| The bank holidays pull failed | Below (Phase 2) |
| Job failed halfway: is it safe to rerun? | Phase 5 |
| CI is red on `main` | Phase 6 |

## Files were uploaded but no new rows appeared

- **Symptom:** the update COMPLETED, but the bronze row counts did not change.
- **Likely causes:** the files are in the wrong folder; the name does not match the loader's filter
  (`*.jsonl`, `*.csv`, `*.json`); the name starts with `_` or `.` (Spark file sources skip those);
  the update ran before the upload finished.
- **Check:**
  1. List the folder: `databricks fs ls dbfs:/Volumes/workspace/bronze/landing/<source>/ -l`
  2. Find the newest ingested file:
     `SELECT max(_source_modified_at), max(_ingested_at) FROM workspace.bronze.<table>`
  3. Read the latest update's events:
     `databricks api get "/api/2.0/pipelines/<pipeline-id>/events?max_results=50&order_by=timestamp%20desc"`
- **Fix:** put the file in the right folder under the right name, then run the pipeline again.
  Do **not** use a full refresh for this: it rebuilds the table from every file and is only needed
  after an incompatible schema change.
- **Prevent:** upload to the final name only once the file is complete.

## The update was cancelled after a new column appeared

- **Symptom:** `bundle run` or a job says "update cancelled", and a new update with cause
  `SCHEMA_CHANGE` appears straight after.
- **What happened (observed 2026-10-04, when `channel` first appeared):** Auto Loader met an unknown
  column and wrote a warning ("encountered a schema change during execution and terminated. A new
  update using the new schema will be automatically started"). The update was cancelled and the
  platform started a new one by itself, which COMPLETED in 19 seconds with every row exactly once.
- **Check:** `databricks pipelines list-updates <pipeline-id> --max-results 3` shows the canceled
  update followed by a COMPLETED `SCHEMA_CHANGE` update. Then run
  `sql/checks/bronze_reconciliation.sql`: every check should pass.
- **Fix:** nothing, if the `SCHEMA_CHANGE` update COMPLETED. If it failed, read its events for the error.
- **Also check:** a type conflict does not evolve the schema. It is rescued into `_rescued_data` and
  counted by the `no_contract_violations` expectation, so look at that metric.

## The bank holidays pull failed

- **Symptom:** a run of the `pull_bank_holidays` job failed.
- **Likely causes:** gov.uk returned an error (the module retries 5xx errors and network failures
  three times, never 4xx); the response was not the expected shape (the module refuses to land
  it); Databricks changed its outbound allow-list.
- **Check:** open the run and read the task output. "payload is not valid JSON" or "missing
  divisions" means the API changed shape. `urllib` errors mean connectivity.
- **Fix:** for a transient error, rerun the job. That is safe: the file name is a hash of the
  content, so a rerun never duplicates. For a changed shape, inspect the payload first, then update
  `validate()` and silver together. Do not loosen the check blindly.
- **Impact:** bronze keeps the last calendar that landed, and nothing downstream breaks.

## Safe rerun

TBD in Phase 5. The design goal is that rerunning any job is always safe (idempotent):
no duplicate rows, no changed results.
