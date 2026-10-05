# Data quality

Quality is checked at four levels, from the cheapest to the most thorough. The last three write to
the platform, and the first is only about the code.

| Level | What it checks | Where it runs | Fails the job? |
|---|---|---|---|
| Tests (about 400) | The code and the pipeline SQL logic, on small fixtures | Laptop and CI | Blocks nothing (see ADR-025) |
| Expectations (54 across the pipeline) | Each row, as the pipeline writes it | Inside the pipeline | Only the `FAIL UPDATE` ones |
| Reconciliation (67 checks) | Table against table: counts, sums, keys | `verify` task, after the pipeline | Yes, any false check |
| Summary | History of the two levels above | `workspace.quality` | No, it only reports |

## The summary (`workspace.quality`)

Created and filled by the `verify` task on every run (ADR-027). Nothing to set up by hand.

| Object | Kind | What it answers |
|---|---|---|
| `check_results` | table | Every check of every run: suite, name, expected, actual, passed |
| `expectation_results` | table | Rows that passed and failed each pipeline expectation in the latest update |
| `check_scorecard` | view | Newest run: checks, passed and failed per suite |
| `check_history` | view | One row per run: did anything fail that day? |
| `expectation_scorecard` | view | Newest snapshot with the failure rate per expectation |

Example: which checks failed most recently?

```sql
SELECT run_at, suite, check_name, expected, actual
FROM workspace.quality.check_results
WHERE NOT passed
ORDER BY run_at DESC;
```

## Known limits

- Recording is best effort. If it fails, the job log says `WARNING: could not record` and the checks
  still decide whether the job passes. A missing day in `check_history` means the write failed, not
  that nothing ran.
- Expectation metrics are the latest update only. A schema-change restart makes a second update, and
  the snapshot then describes the restarted one.
- 17 fraud events sit in the silver quarantine (see the runbook). They are visible there, not hidden.
