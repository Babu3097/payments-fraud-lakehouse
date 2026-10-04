# Design decisions log

Each entry records what we decided and why, including the trade-offs. Newest entries go at the
bottom. Status is one of: Accepted, Open, Superseded.

## ADR-001: Python 3.12 pinned with uv and a lockfile

- **Status:** Accepted
- **Context:** The project needs the same Python and dependency versions on a developer Mac,
  in CI (Linux), and ideally close to what runs on Databricks.
- **Decision:** Pin Python 3.12 (`.python-version`, `requires-python`), manage it with `uv`,
  and commit `uv.lock` so every dependency version is exact.
- **Why:** Databricks serverless environment versions 3 to 6 run Python 3.12.3 (checked against
  the Databricks documentation), so local tests behave like the cloud. The lockfile makes builds
  reproducible and stops silent upstream upgrades breaking a passing build. The macOS system
  Python (3.9) belongs to the OS and is too old for current tooling.
- **Trade-offs:** We must follow Databricks when it moves to a newer Python. Contributors need `uv`.

## ADR-002: One schema per medallion layer, inside one catalog

- **Status:** Accepted
- **Decision:** `workspace.bronze`, `workspace.silver`, `workspace.gold`.
- **Why:** Free Edition provides the `workspace` catalog, and a schema per layer is the simplest
  model that still gives per-layer permissions (for example, analysts get `SELECT` on gold only).
- **Trade-offs:** The boundary between layers is weaker than separate catalogs, which would give
  separate storage locations, workspace binding and cost attribution. For a single-team project
  that is unnecessary overhead. If this grew into dev and prod environments we would add a catalog
  per environment and keep a schema per layer inside each.

## ADR-003: Authentication

- **Status:** Open (CI part still to verify)
- **Decision:** Humans use OAuth user-to-machine login (`databricks auth login`), with the token
  held in the macOS keychain, not personal access tokens.
- **Why:** Short-lived tokens that refresh automatically, and no secret to copy-paste or leak.
  Free Edition has no SSO (sign-in is email OTP, Google or Microsoft), so any second factor comes
  from that identity provider account, for example Google 2-step verification.
- **Open:** Unattended CI would use a service principal (OAuth machine-to-machine, or OIDC
  federation from GitHub) with least-privilege grants. The workspace service-principal API
  responds (the list is empty), but creating one and issuing an OAuth secret is untested, and
  Free Edition has no account console. We test this in Phase 5 or 6. If it is not possible, CI
  stays workspace-free (lint and unit tests only).

## ADR-004: One source of truth for tool versions, same checks in hooks and CI

- **Status:** Accepted
- **Decision:** The pre-commit hooks for ruff and sqlfluff run via `uv run`, so their versions
  come from `uv.lock`. CI runs the same pre-commit hooks, then pytest. The CI runner is pinned
  to `ubuntu-24.04` instead of `ubuntu-latest` (GitHub moves "latest" to Ubuntu 26 on
  19 October 2026), so the operating system image only changes when we choose.
- **Why:** Pinning versions in both a hook config and a lockfile lets them drift, so a commit can
  pass locally and fail in CI. Hooks catch problems in seconds on the laptop. CI is the
  enforcement, because hooks can be skipped with `--no-verify`.
- **Trade-offs:** Hooks need `uv` installed and start slightly slower than isolated hook
  environments.

## ADR-005: One canonical transaction status, with its provenance

- **Status:** Accepted
- **Context:** PaySim has no approved or declined status, but we report an approval rate.
- **Decision:** Silver stores `status` (APPROVED or DECLINED) and `status_source`. For PaySim,
  status is derived: DECLINED when `isFlaggedFraud = 1`, otherwise APPROVED. Generated
  transactions carry their own status from the generator.
- **Why:** The KPI is comparable across the whole history, and the provenance column stops a
  derived value being mistaken for source truth.
- **Trade-offs:** The PaySim approval rate rests on an assumption. Verified on the real file:
  only 16 of 6,362,620 rows are flagged, so the derived rate is 99.99975% and almost constant.
  Real variation comes from generated days. Dashboards must label PaySim status as derived.

## ADR-006: Customer attributes arrive as a change-event feed that builds SCD Type 2

- **Status:** Accepted
- **Context:** PaySim has no customer attributes, so a slowly changing dimension would have
  nothing to track.
- **Decision:** The generator emits `customer_profile_changes` (customer id, segment, region,
  change timestamp): an initial load plus daily changes. Lakeflow Auto CDC turns them into an SCD
  Type 2 dimension. The population size is decided after we count PaySim customers.
- **Why:** Change events are the standard CDC input, and Auto CDC maintains the validity range
  (`__START_AT`, `__END_AT`) and handles out-of-order events through a sequence column.
- **Trade-offs:** The attribute values are synthetic and documented as such. Rejected
  alternative: full daily snapshots, which mean far bigger files and a Python-only API.

## ADR-007: Minimal Asset Bundle from Phase 2

- **Status:** Accepted; completed in Phase 5
- **Decision:** `databricks.yml` plus one pipeline resource (dev target only) in Phase 2. Phase 5
  added the daily job (ADR-021), its schedule and alerts (ADR-022), and a `prod` target that is
  defined and checked with `databricks bundle validate -t prod` but never deployed.
- **Why:** The pipeline is deployed from Git from day one, so there are no hand-made workspace
  resources to migrate later. The prod target is not deployed because Free Edition has one
  workspace and one catalog, so a second copy would write into the same tables as dev. On a paid
  account it would point at its own workspace and catalog and run as a service principal.
- **Trade-offs:** Bundle concepts arrive earlier than the roadmap, and dev mode prefixes
  resource names with the user. Deploying is also destructive on purpose: the Phase 5 deploy
  deleted the old `pull_bank_holidays` job from the workspace because its file was gone from the
  repo. The pipeline SQL names `workspace.` directly, so the `catalog` variable only moves the
  volume path; a real prod catalog would need that SQL parameterised first.

## ADR-008: Fixed time anchor for PaySim

- **Status:** Accepted (revised after profiling the real file)
- **Decision:** PaySim `step` 1 maps to 2026-08-20 00:00:00, adding one hour per step, so step 743
  is 2026-09-19 22:00. The generator continues from 2026-09-20 with one file per day.
- **Why:** `step` is only a relative hour counter (verified range 1 to 743). Profiling showed 93%
  of rows fall on 14 heavy days and the last day has only 272 rows. With this anchor the England and
  Wales summer bank holiday (31 August) is day 11, a heavy day with 349k rows, so the holiday flag has
  real data to show. The first proposal (1 August) put that holiday on day 30 and was rejected.
- **Trade-offs:** The dates are synthetic, and the mapping must be documented wherever dates
  appear. The flag uses England and Wales (a region choice to revisit in Phase 4). All three
  regions stay in bronze.

## ADR-009: One pipeline for all layers, and the API pull runs inside Databricks

- **Status:** Accepted
- **Context:** Free Edition allows one active pipeline per pipeline type and restricts outbound
  internet to trusted domains.
- **Decision:** A single Lakeflow pipeline holds bronze, silver and gold files, with fully
  qualified table names (`workspace.<layer>.<table>`). The bank-holidays pull runs as a
  serverless job task.
- **Why:** It fits the platform limits, and the API call stays inside the platform like a real
  system. A one-off serverless run on 2026-10-04 reached gov.uk (HTTP 200, 22,207 bytes, three
  regions), so the restriction does not block it.
- **Trade-offs:** If Databricks tightens the allow-list the pull would have to move outside the
  workspace. A single pipeline would need splitting if the project outgrew Free Edition.

## ADR-011: Bronze data contract: money as DECIMAL(18,2), strict where the source is trusted

- **Status:** Accepted
- **Decision:** Money columns are `DECIMAL(18,2)`. Bronze tables are typed by an explicit contract
  where the source guarantees its types (PaySim, the customer feed). Where defects are expected,
  the risky column stays a string for silver to parse (`event_ts` in the daily transactions). New
  columns are accepted when they appear (`addNewColumns`), and anything that conflicts with a
  declared type lands in `_rescued_data`. A warn-only expectation, `no_contract_violations`,
  counts those rescues without dropping rows.
- **Why:** Floating point cannot represent money exactly. We checked the real PaySim file rather
  than assume: none of 6,362,620 rows in any of the five money columns has digits beyond two decimal
  places, so the conversion is lossless, and the largest balance (356,179,278.92) fits easily.
- **Trade-offs:** A source that later sends more precision would be rounded, so the contract and
  this record would have to change. Typing at bronze means a bad value is rescued and not kept as
  raw text, which is acceptable because the original file always stays in the landing volume.

## ADR-012: Schema evolution: accept new columns, rescue conflicts, let the platform restart

- **Status:** Accepted (observed 2026-10-04 by hand, and again inside the daily job)
- **Decision:** Auto Loader runs with `addNewColumns`, so an extra column in the daily feed is
  added to the table. A value that conflicts with a declared type is rescued into `_rescued_data`.
  Loaders only read files that match their extension (`*.jsonl`, `*.csv`, `*.json`).
- **Why:** An added column is a non-breaking change, so ingestion must not need a human. We watched
  it happen when `channel` appeared on 28 September: Auto Loader stopped the update, the platform
  cancelled it and started a new update itself (cause `SCHEMA_CHANGE`, 19 seconds), and every row
  landed exactly once (14 days of 40,400 rows, no duplicates, no rescues). The extension filter stops
  a temp or stray file from being read as data.
- **In the daily job (a manual run on 2026-10-04; `device_type` first appears in the 6 October file):** the
  job's pipeline task followed the platform's own restart. The first update was cancelled at 20:54:43
  UTC "due to a schema change in workspace.bronze.transactions_daily, and will be restarted", an
  update with cause `SCHEMA_CHANGE` started three seconds later and COMPLETED at 21:00:04, and the
  task finished SUCCESS on attempt 0 after 484 s (292 s when nothing is new). The job retry was not
  used. We had expected to need it, so this settles the question: a new column needs no special
  handling in the job, and the retry stays as insurance for ordinary failures. `device_type` is in
  bronze for exactly the 40,400 rows of that day, with nothing rescued.
- **Trade-offs:** A schema change costs a second update and a second cold start, about two and a half
  minutes here. Silver does not pick up a new column automatically: it selects columns explicitly, so
  a column becomes visible downstream only by a deliberate change. That is intended: bronze absorbs
  drift without losing data, and the layers with a contract change only by review.

## ADR-013: Silver deduplicates with Auto CDC (SCD Type 1) keyed on event_id

- **Status:** Accepted (results to verify on the first silver run)
- **Decision:** `silver.transactions` is an Auto CDC upsert keyed on `event_id`, sequenced by
  `_ingested_at`, fed only by rows that passed every check.
- **Why:** An upsert is idempotent: a duplicated or replayed event updates the same row instead of
  adding one, a duplicate that arrives in a later file is handled too, and no streaming job has to
  hold all 6.9M keys in state forever. It is also practice for the SCD Type 2 dimension in Phase 4.
- **Trade-offs:** The table is no longer append-only, so a downstream streaming read would need
  `skipChangeCommits`; gold reads silver in batch, so this does not bite. Two identical copies
  share a sequence value, which is harmless because they are identical. A corrected event that
  reuses its `event_id` overwrites the old row, which is the behaviour we want. Rejected
  alternatives: a streaming `dropDuplicates` (unbounded state, no corrections) and a materialized
  view with `ROW_NUMBER` (recomputes the whole dataset on each update).

## ADR-014: Quarantine and expectations policy for silver

- **Status:** Accepted
- **Decision:** A private working table unifies PaySim and the generated feed and records each
  row's failed checks as a list of reason codes (`NULL_REQUIRED_FIELD`, `NEGATIVE_AMOUNT`,
  `UNKNOWN_TYPE`, `BAD_TIMESTAMP`). Rows with an empty list go to `silver.transactions`; the rest go
  to `silver.transactions_quarantine` with the reasons and their original values. Duplicates are
  removed, not quarantined, because a duplicate is not a defect in the data. Expectations on the
  clean table are a safety net: hard invariants use `FAIL UPDATE`, soft checks only measure.
  The customer profile feed has no injected defects, so it uses expectations only.
- **Why:** `ON VIOLATION DROP ROW` would lose rows silently, and a quarantine table keeps every
  rejected row with its reason, so quality is observable and rows can be repaired and replayed. One
  reason code per defect class lets the counts reconcile with the generator manifests exactly.
- **Trade-offs:** Each rule is written twice (the split and the safety-net expectation). A hard
  expectation stops the update, which is right for something that must never reach gold but means
  a bug in the split blocks the pipeline until fixed. The profile feed has a documented gap: a bad
  row there would fail the update and not be quarantined.

## ADR-015: Reference-data join in silver, and a fixed UTC time zone

- **Status:** Accepted
- **Decision:** Each transaction gets `is_bank_holiday` for England and Wales, using a join to
  `silver.bank_holidays` on one row per date. The pipeline sets `spark.sql.session.timeZone` to
  UTC.
- **Why:** The join satisfies the brief and gives silver a ready-to-use flag; the date-level
  `DISTINCT` guarantees a date with two holiday names can never duplicate a transaction. Event
  dates come from timestamps, so the time zone must never depend on a default.
- **Trade-offs:** The flag is England and Wales only, though customers have a region (a possible
  refinement). Gold's `dim_date` will own the flag for reporting, so silver and gold both carry it.
  The static side of a stream-static join is read when each micro-batch starts, so a holiday added
  later does not retroactively change rows already written.

## ADR-016: Every table is replayable from the landing files

- **Status:** Accepted (verified 2026-10-04)
- **Decision:** The landing volume is the system of record for raw files. Files are never edited or
  deleted, and every table is a function of them, so any table can be rebuilt with a full refresh.
- **Evidence:** A full refresh of every table finished in 1 min 45 s. All eight tables came back with
  identical row counts and identical fingerprints over their business columns, the audit timestamps
  were new (the rows were really recomputed), and all 43 reconciliation checks passed again.
- **Why:** If a silver rule has a bug, we fix the rule and rebuild, without refetching any source.
  That is the practical meaning of "bronze is raw and replayable".
- **Trade-offs:** A full refresh reprocesses everything and resets streaming state, which is cheap at
  7 million rows but would not be at billions. Audit columns (`_ingested_at`, `quarantined_at`)
  change on a rebuild, so they must never be used as business keys. Production would also need a
  retention policy for the landing files. On a rebuild every file is read at once, so a column added
  later (`channel`) is known from the start and no schema-change restart happens.

## ADR-017: Gold is a star schema, and a reconciliation view fails the run on any mismatch

- **Status:** Accepted (results to verify on the first gold run)
- **Decision:** `fact_transactions` (grain: one transaction) with three dimensions: `dim_customer`
  (SCD2), `dim_date` and `dim_type`. Surrogate keys are deterministic hashes. An ID with no
  dimension row, which is every PaySim ID, maps to an Unknown member (key -1), and the raw ID stays
  on the fact. A one-row `gold.reconciliation` view compares gold with bronze (rows, amounts to the
  cent, an accounting identity that every raw event is in gold or quarantine, key integrity, KPI
  totals), and each check is a `FAIL UPDATE` expectation. Independent checks in
  `sql/checks/gold_reconciliation.sql` use the landing files instead of bronze.
- **Why:** A star schema keeps queries simple and suits Power BI. Hash keys are stable across
  rebuilds and work in a materialized view, which cannot use identity columns. A failing
  expectation names the check that broke, stops the update, and needs no one to remember to look.
- **Trade-offs:** The Unknown member means PaySim facts cannot be sliced by customer segment or
  region. The reconciliation compares gold with bronze, which was itself reconciled to the landing
  files, so the heavier landing-file check runs separately. A failed update does not hide gold tables
  that already refreshed in the same update; a write-audit-publish pattern (build, check, then
  publish) would, at the cost of a staging copy.

## ADR-018: Gold layout: liquid clustering, not partitioning

- **Status:** Accepted (measured on 2026-10-04)
- **Decision:** `fact_transactions` uses `CLUSTER BY (date_key, type_key)`. No gold table is
  partitioned.
- **Why:** The two keys are what dashboards filter by, a date range and a type. Databricks recommends
  liquid clustering for new tables below roughly a terabyte, and it can change keys later without a
  rewrite. Daily partitions would create tiny files (about 47 partitions, most of them a few MB).
- **Measurement:** three scratch copies of the fact (7.0M rows, 8 MB target file size, compacted with
  OPTIMIZE) were queried, and files and bytes read come from `system.query.history`. The scratch
  schema was dropped afterwards.

  | Query | Plain (5 files) | Partitioned (57 files) | Clustered (25 files) |
  |---|---|---|---|
  | One day | 2 files, 9.6 MB | 1 file, 0.4 MB | 4 files, 5.3 MB |
  | One week and TRANSFER | 1 file, 5.9 MB | 7 files, 2.7 MB | 1 file, 0.9 MB |
  | TRANSFER only | 2 files, 10.3 MB | 47 files, 25.4 MB | 4 files, 3.3 MB |
  | Full scan | 5 files, 23.9 MB | 57 files, 36.2 MB | 25 files, 28.8 MB |

- **Reading it:** partitioning wins only the single-date query and is the worst layout for every
  other one: a type-only query opens 47 files, and a full scan reads about 50% more bytes than the
  plain table. Clustering is best for the realistic mix of date range plus type, and never bad.
- **Trade-offs and caveats:** wall-clock time was 0.6 to 1.4 s for every layout (noise), so at this
  size the effect is bytes read, not speed. The plain copy was not random (its row order happened to
  correlate with date), which understates clustering's benefit. Each query ran once, so the numbers
  are indicative. The production fact is 4 files, so it behaves like the plain copy today; the
  clustering is set for when it grows. Partitioning would still be right for a regulatory need to
  separate data physically or drop whole days.

## ADR-019: SCD Type 2 built by Auto CDC and joined at event time

- **Status:** Accepted (results to verify on the first gold run)
- **Decision:** Auto CDC builds the history from the profile change events with `TRACK HISTORY ON
  segment, region`. A version is valid over the half-open range `[valid_from, valid_to)`, and the
  open end of the current version is 9999-12-31 so a join needs one range test. The fact joins the
  version that was valid at `event_ts`, for both the sender and the recipient.
- **Why:** Reporting by segment or region must reflect where a customer was when the event happened.
  Half-open ranges avoid both a gap and a double count at the boundary. The post-run check recomputes
  every generated event's segment and region straight from the change events, without Auto CDC, and
  must match the dimension.
- **Trade-offs:** Because the fact stores the key of a version, a late-arriving change event needs
  the fact to be recomputed, which a materialized view does. Both attributes are tracked, so a change
  to either creates a version.

## ADR-020: Simple rules are measured against the label, not trusted

- **Status:** Accepted
- **Decision:** The fact carries rule flags (balance drain, night-time high-value transfer, burst,
  and the source's own rule). `gold.rule_effectiveness` reports each rule's precision and recall
  against the fraud label, per source. `gold.unusual_activity` lists entity-days flagged by
  documented thresholds.
- **Why:** A rule is only useful if you know how often it is right and how much fraud it finds. On
  PaySim the source's own rule flags 16 of 8,213 fraud rows, while balance drain flags almost all
  of them, which is a simulation artifact, so the numbers are reported with that caveat.
- **Trade-offs:** The thresholds are our own choices, written at the top of each file, and the
  generated fraud was built from the same three patterns the rules look for, so recall on it is
  flattering. Real data would need rules tuned against real labels.

## ADR-021: One daily job, four tasks, and a verify step that fails the run

- **Status:** Accepted (verified 2026-10-04)
- **Decision:** One Lakeflow Job, `payments_lakehouse_daily`, deployed by the bundle.
  `generate_daily_files` and `pull_holidays` run side by side, then `refresh_pipeline` runs bronze,
  silver and gold as one pipeline update, then `verify` runs every file in `sql/checks/` and fails
  the task if any check is false. The two wheel tasks run this project's own package on serverless
  compute; the pipeline task runs the existing pipeline.
- **Why:** A job exists to make the order explicit: the pipeline cannot start before the day's files
  are there, and nothing reports success before the checks have run. The two inputs do not depend on
  each other, so they run in parallel. `verify` is separate from the pipeline's own `FAIL UPDATE`
  constraints on purpose: those stop one pipeline update, while `verify` re-checks all three layers
  against the landing files and the manifests (66 checks), and its failure is what fails the job and
  sends the alert.
- **Evidence:** a run with nothing new takes 7 min 41 s: generate and holidays about 35 s each in
  parallel, the pipeline 292 s, verify 125 s.
- **Trade-offs:** Each wheel task is its own serverless session, so each pays about 30 s of start-up.
  `verify` runs after gold has already published, so a failed check means the data is in doubt, not
  that it was held back (ADR-017). The standalone `pull_bank_holidays` job is gone; that step now runs
  only as part of this job (`databricks bundle run payments_lakehouse_daily --only pull_holidays`).

## ADR-022: Alerts, retries and limits for the daily job

- **Status:** Accepted (alert path proven 2026-10-04)
- **Decision:** The job emails when a run fails and when a run passes 30 minutes, stops a run at one
  hour, allows one run at a time with a second queued, and retries the pipeline task once after two
  minutes. The recipient is the bundle variable `alert_email`, which defaults to the account that
  deploys and is filled in at deploy time, so no address is ever committed.
- **Why:** Email needs no extra service on Free Edition. Alerting on failure only keeps the signal
  meaningful. Queueing instead of skipping means a manual run near 06:00 cannot silently cost the day.
  The retry covers a transient platform failure of the pipeline, such as no compute at that moment.
- **Evidence:** the alert was tested end to end. One check was changed to demand 1,097 calendar days
  instead of 1,096. `verify` failed with "65 of 66 checks passed" and the named check, the job
  failed, and the email reached the account owner's inbox (confirmed by them). Reverting the check
  (hash identical to the commit), redeploying and repairing the run re-ran only `verify`, which
  passed in about a minute, and the same run turned green.
- **Retries, as observed:** serverless task compute has auto-optimization, which retries failed tasks
  by itself and is on by default. The failing `verify` ran twice (67 s and 49 s) although no retry is
  configured on it. The Databricks docs say job-level notifications are not sent when a failed task is
  retried, so the email arrives only when the run ends. We leave auto-optimization on because every
  task is idempotent (ADR-023), so a retry is safe. The explicit retry on the pipeline task is separate,
  because a pipeline task does not run on that compute.
- **Trade-offs:** A deterministic failure, which a real data problem is, alerts about a minute later
  than it could, and a hard gold failure costs one extra pipeline update. One person gets the email; a
  team would use a shared address or a chat destination. The 30-minute and 1-hour limits are guesses
  from a 7.5-minute run and should be revisited with real run history.

## ADR-023: A rerun is safe by construction, and we proved it

- **Status:** Accepted (verified 2026-10-04)
- **Decision:** Every step is written so that doing it twice gives the same result. The generator
  skips a file whose bytes already match and writes through a temporary file and a rename. The holiday
  file's name is a hash of its content. Auto Loader remembers the files it has read. Silver is an
  upsert on `event_id`. Gold is recomputed from silver. "Yesterday" is measured from the run's own
  start date (passed as `--today`) and not from the wall clock, so the day chosen does not depend on
  when the task happens to run. That this also holds for a repair run after midnight UTC is the
  intent but has not been observed yet.
- **Evidence:** (1) A run for an already-landed day: all three generated files and the holiday file
  said `unchanged`, and 18 of 19 tables were identical in row count and content hash. Only `dim_date`
  changed, from 365 to 1,096 rows, which was the intended change. (2) The three files the cloud wrote
  were hashed against the copies made on the laptop: identical sha256, including the 12.8 MB
  transactions file, so the generator is deterministic across machines. (3) A new day with a new
  column: 8 of 8 predicted row counts were exact. (4) After a failure, a repair re-ran only `verify`.
- **Why:** Jobs fail and get rerun by a tired person at 7am. If a rerun can duplicate or change data,
  the runbook has to say "check first". If it cannot, the answer is always "just rerun".
- **Trade-offs:** The skip compares bytes, so a change to the generator's code would overwrite an
  already-landed file with different content, and Auto Loader would not read it again because it has
  seen that file name. The bronze reconciliation compares bronze with the landing files, so that
  divergence would fail the run, but the cure is deliberate (a new file name or a full refresh). A
  real source file is never rewritten, so this is a synthetic-data problem only.

## ADR-024: The calendar must outlast the job

- **Status:** Accepted
- **Decision:** `dim_date` covers 2026-01-01 to 2028-12-31 (1,096 days), the silver event-date bound
  moves to the same end date, and the gold reconciliation expects 1,096 days.
- **Why:** Found while designing the job, before it could bite. `dim_date` held only 2026, and a daily
  job never stops. On 2027-01-01 the first transaction would have had no date row, `no_orphan_keys`
  would have failed, and every run after that would have failed too. A one-off pipeline can use a
  fixed range; a scheduled one cannot.
- **Trade-offs:** The same time bomb now sits at the end of 2028, where the holiday API also stops,
  so the runbook says to extend both together. Building the calendar from the first and last fact date
  would hide the holiday coverage problem instead of fixing it, so the range stays explicit and the
  check that would catch it stays strict.

## ADR-010: A deterministic generator with a manifest as ground truth

- **Status:** Accepted
- **Decision:** Each day is seeded from (seed, date), uses only the standard library, writes files
  atomically (temp file, then rename), and writes a manifest of exactly what it injected.
  Customer attributes come from replaying the change events from the initial load, so any single
  day can be regenerated on its own.
- **Why:** Identical output for the same day makes reruns idempotent and tests exact. Atomic writes
  mean a half-written file can never be picked up by Auto Loader. The manifest turns data quality
  into something provable: silver's quarantine count can be compared with the injected defects.
- **Trade-offs:** The data is synthetic, with defect rates and fraud patterns that we chose, so
  the findings describe the pipeline and not the real world. The balances are a per-row snapshot,
  not a ledger. Replaying state costs a little time, which is irrelevant at 65,000 entities.
