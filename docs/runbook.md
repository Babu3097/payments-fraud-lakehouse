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
| The update failed on a gold reconciliation check | Below (Phase 4) |
| A customer change arrived late | Below (Phase 4) |
| Gold takes longer than expected | Below (Phase 4) |
| The update failed on a hard expectation | Below (Phase 3) |
| The quarantine table grew unexpectedly | Below (Phase 3) |
| Fraud events are missing from the KPIs | Below (Phase 3) |
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

## The update failed on a hard expectation

- **Symptom:** the update FAILED and the event log names an expectation such as `known_type` or
  `amount_not_negative` on `silver.transactions`.
- **What it means:** a row that should have been quarantined reached the clean table, so the split in
  `transactions_unified.sql` has a gap. Hard rules exist to stop that data reaching gold.
- **Check:** read the failure in the update's events
  (`databricks api get "/api/2.0/pipelines/<pipeline-id>/events?max_results=50&order_by=timestamp%20desc"`),
  then look for rows that break the rule in `silver.transactions_unified`.
- **Fix:** add the missing check to `failed_checks`, redeploy, and rerun. Nothing was written: the
  update is atomic, so the clean table is untouched.
- **Prevent:** every hard expectation has a matching reason code, and the reconciliation compares
  each code with the manifests.

## The quarantine table grew unexpectedly

- **Symptom:** `silver.transactions_quarantine` gained far more rows than usual in one update.
- **Likely causes:** a source change (a new type value, a different timestamp format, a field that is
  now null), a wrong rule, or a replayed file whose bad rows are quarantined again.
- **Check:** count by reason and by day:
  `SELECT failed_checks[0], left(substring_index(_source_file,'transactions_',-1),10), count(*) FROM workspace.silver.transactions_quarantine GROUP BY 1, 2`.
  Read a few `raw_record` values. Null fields are shown explicitly as `null`.
- **Fix:** if the rule is wrong, correct it and refresh the quarantine table with
  `databricks bundle run payments_pipeline --full-refresh workspace.silver.transactions_quarantine`.
  If the source is wrong, ask the source team to resend corrected rows as a new file.
- **Normal level:** about 1.1% of generated rows (440 of 40,000 a day), exactly the injected defects.

## Fraud events are missing from the KPIs

- **Symptom:** the fraud count in gold is lower than the labelled fraud in the source.
- **Cause:** a fraud event that also has a defect sits in quarantine. In the first 14 days, 17 of
  1,680 generated fraud rows were quarantined (PaySim's fraud rows are all in silver).
- **Check:** `SELECT count(*) FROM workspace.silver.transactions_quarantine WHERE get_json_object(raw_record, '$.is_fraud') = 'true'`.
- **Fix:** repair the rows at the source and resend them as a new file (see docs/silver.md).
- **Prevent:** alert when quarantined fraud rows are above zero, since each one hides a fraud event.

## The update failed on a gold reconciliation check

- **Symptom:** the update is FAILED and the message reads "Update ... is FAILED since flow
  workspace.gold.reconciliation failed an expectation check", followed by "Violated expectations:
  '<check name>'".
- **Seen for real (a deliberate test on 2026-10-04):** the message also prints the **whole control-total
  record** (gold and bronze rows, amounts, the accounting identity, orphan counts), so the failed
  check and the numbers behind it are in the failure itself. Read the numbers first.
- **What it means:** gold no longer agrees with the data it was built from. Do not publish gold.
  Note that the other gold tables had already refreshed in the same update and stay visible
  (ADR-017), so tell consumers if the data is in doubt.
- **Check:** compare the two sides of the named check. For example, `paysim_rows_match` compares
  `paysim_rows_gold` with `paysim_rows_bronze`; `generated_events_accounted_for` expects
  `bronze_generated_events = gold_generated_rows + quarantined_events`. Then run
  `sql/checks/gold_reconciliation.sql` for the independent view from the landing files.
- **Likely causes:** a silver rule changed and rows moved between gold and quarantine, a dimension key
  stopped resolving (orphan counts above zero), or a source file was added or replaced outside the
  normal flow.
- **Fix:** correct the cause, rerun, and confirm the update COMPLETES. Never loosen a constraint to
  make a run pass.
- **Test it:** the failure path was proven by making one constraint demand `bronze + 1`, watching the
  update fail with the check named, reverting, and rerunning to green.

## A customer change arrived late

- **Symptom:** a profile change event is dated earlier than transactions that were already loaded.
- **Expected behaviour (tested):** Auto CDC re-cuts the customer's history in the middle, and the
  fact table, a materialized view, re-points only the affected transactions. In the test, four late
  events moved exactly 173 sender keys and 98 recipient keys, and every other row was unchanged.
- **Check:** the SCD2 checks in `sql/checks/gold_reconciliation.sql`: no gaps or overlaps, one current
  version per customer, and the point-in-time test that recomputes each customer's attributes at
  event time from the events themselves.
- **Fix:** none needed. If the point-in-time check fails, a version range is wrong.
- **Note:** a late file has no manifest. The bronze reconciliation accounts for files named
  `*_late.csv` by counting them in the landing folder.

## Gold takes longer than expected

- **What is normal today:** every gold materialized view is **recomputed in full** on each update (the
  refresh type is `RECOMPUTED`), because the joins, windows and non-row-tracked sources prevent an
  incremental refresh. At 7 million rows a whole update takes about 2 to 5 minutes including cold start.
- **Check:** the update's timeline in the pipeline UI, and `DESCRIBE EXTENDED workspace.gold.<name>`
  for the last refresh type.
- **If it grows:** enable row tracking on the sources and check which operations block incremental
  refresh, or split the heaviest view (the fact) so only it is recomputed.

## Safe rerun

TBD in Phase 5. The design goal is that rerunning any job is always safe (idempotent):
no duplicate rows, no changed results.
