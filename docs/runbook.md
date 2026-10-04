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
| Auto Loader processed no new files, or the schema changed | Phase 2 |
| Bank holidays API unavailable or returned an unexpected format | Phase 2 |
| Job failed halfway: is it safe to rerun? | Phase 5 |
| CI is red on `main` | Phase 6 |

## Safe rerun

TBD in Phase 5. The design goal is that rerunning any job is always safe (idempotent):
no duplicate rows, no changed results.
