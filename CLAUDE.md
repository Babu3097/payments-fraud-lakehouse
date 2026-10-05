# CLAUDE.md: project memory

Read this first in every session. Keep the **Progress log** at the bottom up to date.

## What this project is

A **Payments Fraud-Monitoring Lakehouse** on Databricks Free Edition: PaySim data plus a Python
daily-file generator plus the GOV.UK bank holidays API, processed incrementally through
bronze, silver and gold (star schema) and surfaced in Power BI and an AI/BI dashboard. It is a
portfolio project for moving from data analytics/BI into UK data engineering.

## Working agreement (how Claude should behave)

- **Role:** senior data engineer mentor and pair programmer. The user must be able to explain
  and defend every design decision in an interview.
- Explain the **why** before each step, especially engineering concepts (Delta Lake, medallion,
  incremental processing, idempotency, CDC, SCD2, data contracts, partitioning, CI/CD).
- **Ask before** installing, creating, changing or deleting anything. Pause for a clear yes
  before anything outward-facing (GitHub pushes, publishing).
- The user does all **sign-ups, logins and passwords**. Never enter or handle credentials.
- **Free tier only.** Warn before anything that could cost money.
- The user is an experienced analyst (SQL, Python, Power BI, DAX, star schema): skip those
  basics. They are new to the terminal, Git, Claude Code and the Databricks CLI: explain simply.
- Work **one phase at a time** and wait for the user's OK between phases. After each phase ask
  3 interview-style questions, and correct the answers honestly.
- No tutorial copy-paste: every file must be written for this project and explainable.
- Use the user's own browser preference: the built-in **Claude browser pane** for web tasks.

## Phases

0 Setup · 1 Skeleton · 2 Bronze · 3 Silver · 4 Gold · 5 Automate · 6 Quality · 7 Show it
(details in README roadmap)

## Notes files to keep updated

- `LEARNING_NOTES.md` (committed): concepts learned, in plain words.
- `private/INTERVIEW_PREP.md` and `private/CV_BULLETS.md`: **gitignored, never commit.**
  Likely questions with answers and trade-offs, a "challenge I solved" story, how AI was used
  with human review, and 3-4 honest CV bullets with real numbers only (rows, run time, tests, checks).

## Layout

`src/` tested Python · `pipelines/` runs on Databricks · `sql/` SQL · `resources/` Asset Bundle
YAML · `tests/` pytest · `docs/` architecture, data model, setup, runbook, `decisions.md` (ADRs).

## Commands

```bash
uv sync                              # install pinned deps into .venv
uv run pytest                        # unit tests
uv run pre-commit run --all-files    # ruff + sqlfluff + hygiene hooks (same as CI)
databricks current-user me           # confirm Databricks login (profile DEFAULT)
databricks bundle validate --strict -t dev   # check the bundle YAML (prod validates too, never deploy it)
databricks bundle deploy -t dev      # builds the wheel; the daily schedule is LIVE after this
databricks bundle run payments_lakehouse_daily -t dev [--params run_date=YYYY-MM-DD] [--only verify]
```

## Conventions

- Unity Catalog objects: `workspace.bronze`, `workspace.silver`, `workspace.gold` (ADR-002).
- Python 3.12 (ADR-001), line length 100, ruff and sqlfluff (Databricks dialect).
- Record every significant decision in `docs/decisions.md`.
- Never commit data (`data/`, `*.csv`, `*.parquet`), `.env`, tokens, or the workspace URL.
- Commits: short imperative subject. From Phase 2, one branch plus pull request per phase so CI
  runs on PRs.

## Environment facts

- macOS 14 on Apple Silicon. Homebrew `/opt/homebrew/bin`, uv `~/.local/bin`, gh
  `~/.npm-global/bin`. A terminal tab opened before PATH edits will not see them.
- Databricks CLI profile `DEFAULT` (OAuth, token in the keychain). Host lives only in
  `~/.databrickscfg`. Free Edition has a `workspace` catalog and a serverless starter warehouse.
- The Databricks AI Tools plugin is installed at project scope (`.claude/settings.json`).

## Progress log

- **Phase 0 (done):** Homebrew, uv (Python 3.12), Databricks CLI, GitHub login, Databricks OAuth
  login and a test SQL query, AI Dev Kit plugin. Gotcha: Homebrew compiles from source on
  macOS 14, so uv was installed with the vendor installer.
- **Phase 1 (done):** repo skeleton, 10 pre-commit hooks (proven to reject bad code, a fake
  private key and a 1 MB file), CI workflow, docs and 4 ADRs. 22 files pushed to the public repo
  and the first CI run was green in 19 s. Checked against Databricks docs: serverless
  environments 3-6 run Python 3.12.3.
- **Phase 2 (in progress, branch `phase-2-bronze`):** decisions are in ADR-005 to ADR-009.
  Done: schemas `workspace.bronze/silver/gold`; managed volume `workspace.bronze.landing`
  (`sql/01_create_landing_volume.sql`); gov.uk is reachable from serverless; PaySim downloaded,
  verified and profiled (`docs/data_profile.md`, data kept in gitignored `data/raw/paysim/`);
  uploaded to `/Volumes/workspace/bronze/landing/paysim/` and verified (493,534,783 bytes; Databricks
  reads 6,362,620 rows, 8,213 fraud, 16 flagged, step 1 to 743, all equal to the local profile).
  Time anchor: step 1 = 2026-08-20 00:00 (ADR-008), so the generator starts at 2026-09-20.
  Generator done (`src/payments_lakehouse/generator.py`, `docs/generator.md`, ADR-010): 11 tests
  pass, 14 days generated locally in `data/generated/` (not uploaded yet): 560,000 unique events,
  69,225 profile rows. Decided: SCD2 for generator customers only (about 60k + 5k merchants), 40k
  rows a day with documented defects, `channel` field from 2026-09-28.
  Upload plan: batch 1 (20 to 23 Sep), batch 2 (24, 26, 27 Sep, skipping 25), batch 3 (the late
  25 Sep file plus 28 Sep onwards), so the pipeline meets late data and the schema change.
  First cycle done (2026-10-04): bundle deployed to the dev target (pipeline
  `payments_lakehouse_pipeline`, job `pull_bank_holidays`); the holidays job landed its file in 34 s;
  batch 1 uploaded; the first pipeline update COMPLETED in 97 s (PaySim 6.36M rows in about 21 s);
  `sql/checks/bronze_reconciliation.sql` passes 18 of 18 and the expectation metrics show 0 failed.
  Gotcha: the pipeline `libraries.glob` must be a plain `**` (the API rejects `**/*.py`), and
  `bundle validate` cannot catch server-side rules like that.
  Incremental proof done (2026-10-04): run A (nothing new) added 0 rows in 25 s; run B added
  121,200 transaction and 975 profile rows; run C met the late 25 Sep file and the new `channel`
  column: the update was cancelled and the platform restarted it itself (cause SCHEMA_CHANGE,
  19 s), giving 14 days x 40,400 rows exactly once, 18 of 18 checks passing (ADR-012, runbook).
  Written locally but not yet deployed: `pathGlobFilter` on the four loaders.
  Next: deploy that hardening and rerun (expect 0 new rows), push the branch and open the PR
  (ask first), Phase 2 interview questions, then wait for the user's OK before Phase 3.
- **Phase 2 (done, merged in PR 1):** see above. Bronze is deployed and verified.
- **Phase 3 (built and verified on branch `phase-3-silver`, PR pending):** decisions in ADR-013 to
  ADR-016. `pipelines/silver/` is deployed: private `transactions_unified` (two flows),
  `transactions` via Auto CDC SCD1 on `event_id`, `transactions_quarantine` (reason codes, null
  fields explicit), `customer_profile_events`, `bank_holidays`. First run 1 min 54 s and every
  prediction exact (6,916,460 silver, 6,160 quarantined, 5,600 duplicates removed, 280 holidays).
  A rerun with nothing new left all 8 tables identical (content fingerprints); a new day (4 Oct)
  gave +39,560 silver and +440 quarantine; a full refresh rebuilt every table in 1 min 45 s with
  identical content. 43 reconciliation checks pass (25 silver, 18 bronze) and 21 expectations all
  show 0 failed. Finding: 17 fraud events sit in quarantine (see runbook). Generated data now
  spans 20 Sep to 4 Oct (15 days, files in the gitignored `data/generated/`).
  Next: push the branch and open the PR (ask first), Phase 3 interview questions, wait for the
  user's OK before Phase 4 (gold).
- **Phase 3 is merged (PR 2).** Phase 4 follows.
- **Phase 4 (done, merged in PR 3):** decisions in ADR-017 to
  ADR-020. `pipelines/gold/` is deployed: `dim_date` (3 regions), `dim_type`, `dim_customer` (Auto
  CDC SCD2 into a private `customer_history`, hash keys, Unknown member), `fact_transactions`
  (point-in-time join, rule flags, `CLUSTER BY (date_key, type_key)`), four KPI views,
  `unusual_activity`, `rule_effectiveness` and `reconciliation` (12 constraints, 11 hard FAIL
  UPDATE). First gold run 2 min 17 s, all 9 predictions exact. Proofs done: a rerun with nothing new
  left all 11 gold objects identical; a deliberately broken constraint made the update FAIL with
  `paysim_rows_match` named (other gold tables had already refreshed), then reverted and rerun green;
  a new day (5 Oct) plus 4 late-arriving profile events moved exactly 173 sender and 98 recipient
  keys and left 6,955,610 other rows identical. 66 checks pass (19 bronze, 25 silver, 22 gold), 29
  gold expectations 0 failed (read with `event_log()` SQL). Generated data now spans 20 Sep to 5 Oct
  (16 days) plus `customer_profile_2026-09-25_late.csv` (4 late events; bronze checks account for
  `*_late.csv`). The layout experiment is in ADR-018.
- **Phase 5 (built and verified on branch `phase-5-automate`, PR pending):** decisions in ADR-021 to
  ADR-024 (ADR-007 and ADR-012 updated). `resources/daily.job.yml` is deployed as `[dev <user>]
  payments_lakehouse_daily` (job id 53886104526697): `generate_daily_files` + `pull_holidays` then
  `refresh_pipeline` (1 retry after 2 min) then `verify` (66 checks, fails the job). 06:00
  Europe/London, **UNPAUSED (live)**, failure and 30-minute duration emails to the deploying account
  (bundle variable `alert_email`, never committed), 1 h timeout, 1 run at a time with a queue. New
  entry points `generate-day` (skips identical files, `--today` anchor) and `run-checks`; feed field
  `device_type` from 2026-10-06; `dim_date` extended to 2028 (a time bomb on 2027-01-01, ADR-024). The
  deploy deleted the old `pull_bank_holidays` job. `prod` target validated, not deployed. Proofs on
  2026-10-04, predictions written first: a rerun of a loaded day left 18 of 19 tables identical
  (`dim_date` 365 to 1,096 by design); a new day (6 Oct, new column) matched 8 of 8 counts, the files
  the cloud wrote for it were sha256-identical to ones generated on the laptop, and the pipeline task
  rode through the SCHEMA_CHANGE
  restart (484 s, no retry used; closes the ADR-012 question; my 60% guess was wrong); a deliberate
  `verify` failure sent the alert (the user confirmed the email), a repair re-ran only `verify`, and
  the revert was hash-identical. Finding: serverless auto-optimization retries failed Python tasks
  (verify ran twice). Landing holds 17 days (20 Sep to 6 Oct), fact 7,035,140 rows, 66 checks pass,
  45 tests pass. Helper scripts for snapshots live in the session scratchpad, not the repo.
  **Open follow-ups:** (1) first scheduled run is due 2026-10-05 06:00 London: check the Runs tab
  (runs on 5, 6 and 7 Oct land 4, 5, 6 Oct unchanged; the 8 Oct run lands 7 Oct, new data). (2) After
  00:00 UTC test repair across midnight: `databricks jobs repair-run 597513222094068
  --rerun-tasks generate_daily_files`, then see whether it names 2026-10-03 (the start-date anchor
  holds) or 2026-10-04 (wall clock); until then docs call it intent. (3) Interview questions Q16 to
  Q18 for Phase 5; warm-ups A and B and Q13 to Q15 are still open. Next: PR (ask first), then wait
  for the user's OK before Phase 6.
- **Phase 5 is merged (PR 4).**
- **Phase 6 (built locally on branch `phase-6-quality`, NOTHING PUSHED OR DEPLOYED):** decisions in
  ADR-025 to ADR-027. Done: about 400 tests (240 SQL in local PySpark with Temurin 17 in
  ~/.local/jdk-17, 168 other, 99% coverage, floor 95); three silver findings fixed in the SQL (the
  full SQL suite passed in 188 s); a 10-mutation round (9 killed, 1 equivalent mutant, in ADR-025);
  identifier guard hook (patterns in the gitignored private/forbidden_patterns.txt); CI split into
  lint, unit, sql jobs; the data quality summary written locally (`sql/quality/`, `checks.record`,
  verify task parameters, `docs/data_quality.md`, `tests/sql/test_quality_views.py`).
  NOT DONE (needs the user's approval, it touches the live workspace): deploy to dev; a fresh content
  fingerprint baseline first, then a full refresh of silver and gold with fingerprints identical and
  the 67 checks passing; a job run that fills `workspace.quality` (the `event_log()` snapshot SQL is
  untested locally and may need a fix); then push the branch and open the PR (ask first), and Phase 6
  interview questions (Q16 to Q18 from Phase 5 are also unanswered). The runs on 5 to 7 Oct of the
  live schedule are still to be checked (follow-ups 1 and 2 under Phase 5).
  Gotchas: macOS allows only 1333 threads per user, so the SQL tests cap Spark's threads and
  checkpoint each table; run `uv run pytest -m sql` for Spark tests and `-m "not sql"` for the rest;
  saved tables outlive a Spark test, so tests drop them first; macOS has no `timeout` command.
- **Reminder for Phase 7:** confirm the GOV.UK data licence wording for the README credit.
