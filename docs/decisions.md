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

- **Status:** Accepted
- **Decision:** `databricks.yml` plus one pipeline resource (dev target only) now. Phase 5 adds
  the job, schedule, alerts and the prod target.
- **Why:** The pipeline is deployed from Git from day one, so there are no hand-made workspace
  resources to migrate later.
- **Trade-offs:** Bundle concepts arrive earlier than the roadmap, and dev mode prefixes
  resource names with the user.

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

- **Status:** Accepted (observed 2026-10-04); the Phase 5 consequence is still to verify
- **Decision:** Auto Loader runs with `addNewColumns`, so an extra column in the daily feed is
  added to the table. A value that conflicts with a declared type is rescued into `_rescued_data`.
  Loaders only read files that match their extension (`*.jsonl`, `*.csv`, `*.json`).
- **Why:** An added column is a non-breaking change, so ingestion must not need a human. We watched
  it happen when `channel` appeared on 28 September: Auto Loader stopped the update, the platform
  cancelled it and started a new update itself (cause `SCHEMA_CHANGE`, 19 seconds), and every row
  landed exactly once (14 days of 40,400 rows, no duplicates, no rescues). The extension filter stops
  a temp or stray file from being read as data.
- **Trade-offs:** The first update after a new column is reported as cancelled, so a scheduled job
  could look failed although the data completed. Phase 5 adds a job retry and we test it. Silver
  does not pick up a new column automatically: it selects columns explicitly, so a column becomes
  visible downstream only by a deliberate change.

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
