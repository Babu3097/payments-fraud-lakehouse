# Runbook: what to do when it fails

> **Status:** written through Phase 5. Each scenario was written in the phase that built the
> feature, from something that really happened or was tested on purpose. The CI scenario is added
> in Phase 6.

## How you find out

A failed run of the daily job sends an email from Databricks to the account that deployed the
bundle, with the job name in it (tested on 2026-10-04: the account owner confirmed it arrived in
the inbox). A run that is still going after 30 minutes sends a warning to the same address.
Start in the Databricks **Jobs and Pipelines** UI, open the failed run, and find the first failed
task. The API reports a failed run as `INTERNAL_ERROR` with result `FAILED`; the UI just says Failed.

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
| The daily job failed | Below (Phase 5) |
| Is it safe to rerun? | Below (Phase 5) |
| Land a particular day by hand | Below (Phase 5) |
| Pause or resume the schedule | Below (Phase 5) |
| No alert email arrived | Below (Phase 5) |
| The calendar runs out | Below (Phase 5) |
| CI is red on `main` | Below (Phase 6) |

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
- **In the daily job (tested 2026-10-04 with `device_type`):** you will not see a failure. The
  pipeline task waits for the follow-up update and ends SUCCESS on its first attempt, only slower
  (484 s against 292 s), and the retry is not used. The event log tells the story: "has been
  cancelled due to a schema change in workspace.bronze.transactions_daily, and will be restarted".
- **Fix:** nothing, if the `SCHEMA_CHANGE` update COMPLETED. If it failed, read its events for the error.
- **Also check:** a type conflict does not evolve the schema. It is rescued into `_rescued_data` and
  counted by the `no_contract_violations` expectation, so look at that metric.

## The bank holidays pull failed

- **Symptom:** the `pull_holidays` task of the daily job failed.
- **Likely causes:** gov.uk returned an error (the module retries 5xx errors and network failures
  three times, never 4xx); the response was not the expected shape (the module refuses to land
  it); Databricks changed its outbound allow-list.
- **Check:** open the run and read the task output. "payload is not valid JSON" or "missing
  divisions" means the API changed shape. `urllib` errors mean connectivity.
- **Fix:** for a transient error, repair the run (below). That is safe: the file name is a hash of
  the content, so a rerun never duplicates. To run just this task:
  `databricks bundle run payments_lakehouse_daily -t dev --only pull_holidays`. For a changed shape, inspect the payload first, then update
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

## The daily job failed

- **Symptom:** the failure email, or a red run in the Jobs UI. The job has four tasks, in this
  order: `generate_daily_files` and `pull_holidays` (side by side), `refresh_pipeline`, `verify`.
- **First step:** open the run and find the red task. Everything upstream of it succeeded, and
  everything after it did not start. For the text a task printed:
  `databricks jobs get-run <run-id> -o json` lists the task run ids, then
  `databricks jobs get-run-output <task-run-id>` prints its output and error.
- **By task:**
  - `generate_daily_files` prints one `wrote:` or `unchanged:` line per file. It fails on a date
    before 2026-09-20 (the initial load), a `run_date` that is not `yesterday`, `today` or
    `YYYY-MM-DD`, or a volume problem.
  - `pull_holidays`: see "The bank holidays pull failed".
  - `refresh_pipeline`: open the pipeline update and read its events. A hard expectation is "The
    update failed on a hard expectation", a failed reconciliation is "The update failed on a gold
    reconciliation check", and a new column is not a failure at all (see its scenario). The task
    already retried once after two minutes.
  - `verify` prints "N of 66 checks passed" and one line per failure, such as
    `FAILED [gold_reconciliation] dim_date: calendar days: expected 1097, actual 1096`. The name in
    brackets is the file in `sql/checks/`; run that file in the SQL editor to see every row.
- **Fix, then repair:** fix the cause, then use **Repair run** in the UI, or
  `databricks jobs repair-run <run-id> --rerun-all-failed-tasks`. A repair re-runs only the failed
  tasks (and any that depended on them). Tested on 2026-10-04: after a deliberate `verify` failure,
  the repair re-ran `verify` alone, in about a minute, and the same run turned green.
- **Expect two attempts:** serverless auto-optimization retries a failed Python task by itself, so
  a failing `verify` shows two attempts (67 s and 49 s in the test) before the run is marked failed
  and the email is sent. Both attempts print the same output. Job-level notifications are not sent
  for each failed attempt, only when the run ends.
- **If the data is in doubt:** gold publishes before `verify` runs (ADR-017), so a failed `verify` means
  consumers may have seen numbers that do not reconcile. Tell them, then fix and repair.

## Is it safe to rerun?

Yes, at any point, including after a failure halfway. Each step is idempotent (ADR-023):

| Step | Why a second run changes nothing |
|---|---|
| `generate_daily_files` | Skips a file whose bytes already match, and writes by temporary file and rename, so a half-written file is never visible. |
| `pull_holidays` | The file name is a hash of the content, so the same calendar is the same file. |
| `refresh_pipeline` | Auto Loader remembers each file it has read, silver is an upsert on `event_id`, and gold is recomputed from silver. |
| `verify` | Only reads. |

Proved on 2026-10-04: a run for a day that had already landed left 18 of 19 tables identical in
row counts and content hashes (the 19th, `dim_date`, grew from 365 to 1,096 rows, as intended).

- **Repair run or Run now?** Repair continues a failed run and keeps what already succeeded. Run now
  starts a new run from the top, which is also safe, but pays for all four tasks (about 8 minutes).
- **What not to do:** do not run a full refresh to "make sure". It rebuilds every table and is only
  for a rule change (ADR-016). Do not edit or delete landing files, and do not delete bronze tables
  by hand: Auto Loader would then skip files it believes it has already read.

## Land a particular day by hand

- **When:** a missed day, a test, or a backfill. Dates start at 2026-09-20.
- **Command:**
  ```bash
  databricks bundle run payments_lakehouse_daily -t dev --params run_date=2026-10-06
  ```
  In the UI, use Run now with different parameters and set `run_date`. The default is `yesterday`,
  measured from the run's start date and not from the wall clock. If you repair a failed
  `generate_daily_files` on a later day, check that its output names the day you expect; if it does
  not, use Run now with an explicit `run_date`.
- **Safe for a day that already landed:** the files say `unchanged` and nothing is added.
- **Out of order:** a day older than the newest one is fine; SCD2 history is re-cut (see "A customer
  change arrived late").

## Pause or resume the schedule

- **How:** set `pause_status: PAUSED` (or `UNPAUSED`) in `resources/daily.job.yml` and run
  `databricks bundle deploy -t dev`. Do not toggle it only in the UI: the YAML is the source of truth
  and the next deploy would undo it.
- **Check:** `databricks jobs get <job-id> -o json` shows `schedule.pause_status` and the cron
  expression (`0 0 6 * * ?`, Europe/London).
- **Remember:** a paused job also stops sending alerts, because nothing runs. Resume it when the
  reason for pausing is gone.

## No alert email arrived

- **Check in this order:** (1) the spam and promotions folders of the account that deployed; (2) the
  job's settings in the UI, under Notifications, for the failure recipient; (3) that the run has
  ended. A job-level alert is sent when the run ends, not after each failed attempt, so a run that
  is still retrying has not alerted yet. By default a cancelled or skipped run alerts too.
- **Where the address comes from:** the bundle variable `alert_email`, which defaults to the deploying
  account and is filled in at deploy time. To send it elsewhere:
  `databricks bundle deploy -t dev --var alert_email=<address>`. Never put an address in the repo.
- **Until it is fixed:** look at the Runs tab each morning. The schedule is 06:00 London.

## The calendar runs out

- **Symptom:** from 2029-01-01 the pipeline fails on `no_orphan_keys`, or silver quarantines rows
  whose event date is outside the range, and `verify` fails.
- **Cause:** `dim_date` and the silver date bound both end on 2028-12-31 (ADR-024). The GOV.UK
  holiday data also ends there.
- **Fix, before it happens:** extend the end of the `SEQUENCE` in `pipelines/gold/dim_date.sql`, the
  upper bound of `event_date_in_range` in `pipelines/silver/transactions.sql`, the expected number of
  days in `sql/checks/gold_reconciliation.sql`, and check the holiday API covers the new years.

## CI is red on `main`

- **Symptom:** the CI badge in the README is red, or a pull request shows a failed check.
- **Find the step:** `gh run list --branch main --limit 3`, then `gh run view <run-id> --log-failed`.
  The workflow (`.github/workflows/ci.yml`) installs the locked dependencies, runs the same
  pre-commit hooks as a local commit, then runs the tests.
- **Reproduce it on your machine before changing anything:** `uv sync`, then
  `uv run pre-commit run --all-files`, then `uv run pytest`. If it fails the same way, fix it
  locally. If it only fails in CI, the difference is the environment (see below).
- **Common causes:**
  - `uv sync --locked` fails: `pyproject.toml` changed without the lockfile. Run `uv lock` and commit
    `uv.lock`.
  - A hook rewrote a file (formatting, a trailing newline): run the hooks locally and commit the result.
  - A SQL test is skipped or fails only in CI: the SQL tests need Java 17 to 21 and PySpark. Locally they
    skip with a message if Java is missing, so a pass on a machine without Java proves nothing about them.
  - The runner or an action changed: the runner image is pinned (`ubuntu-24.04`), so this only happens
    when someone changes the pin.
- **Fix:** fix forward in a pull request, or revert the commit that broke it. `main` is not protected,
  so nothing stops a merge on red: a failed check on a pull request is advice, not a gate (a decision
  recorded for Phase 6).
- **Prevent:** run `uv run pre-commit run --all-files` and `uv run pytest` before pushing.
