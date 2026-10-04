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
- **Phase 3 (in progress, branch `phase-3-silver`):** decisions in ADR-013 to ADR-015. Written and
  lint-clean, not yet deployed: `pipelines/silver/` (`transactions_unified` private working table
  with two flows, `transactions` via Auto CDC SCD1, `transactions_quarantine`,
  `customer_profile_events`, `bank_holidays`) and `sql/checks/silver_reconciliation.sql`.
  Predictions to verify: silver 6,916,460 rows, quarantine 6,160, duplicates removed 5,600,
  holidays 280. Next: deploy, dry-run validate, run, verify against the manifests.
- **Reminder for Phase 7:** confirm the GOV.UK data licence wording for the README credit.
