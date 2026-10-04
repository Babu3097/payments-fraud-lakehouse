# Learning notes

Concepts I have learned on this project, in plain words. These entries are drafted with Claude
from our sessions. **Rewrite each one in your own wording** as it sinks in, because that is what
makes it stick for interviews.

## Phase 0: setup and foundations

### Two ways to log in to Databricks
- A **personal access token** is a long-lived secret string. Handy for scripts, but if it leaks,
  whoever holds it is me until it is revoked.
- **OAuth** (what `databricks auth login` does) opens the browser, uses my normal login, and gets
  a short-lived token that refreshes automatically. I never copy a secret, and it is stored in the
  macOS keychain. Free Edition has no SSO (sign-in is email OTP, Google or Microsoft), so any
  2-step verification comes from that account.
- A pipeline in CI cannot click "Authorize", so it needs a **service principal**: a machine
  identity with only the permissions it needs, and its secret kept in GitHub Secrets.

### Unity Catalog: catalog, schema, table
- `catalog.schema.table` is a three-level namespace. A **catalog** is the top-level container and
  governance boundary, a **schema** groups related objects, and a **table** holds the rows (schemas
  can also hold views, volumes, functions and models).
- Medallion layers are something *I* model with schemas. Unity Catalog does not treat them specially.
- I chose one schema per layer because it is simple and lets me grant analysts access to gold only.

### Reproducible environments
- A **virtual environment** keeps one project's packages separate from every other project and
  from the Mac's own Python.
- A **lockfile** (`uv.lock`) pins the exact version of every dependency, so my Mac, a teammate's
  laptop and the CI runner all get the same thing. That removes "works on my machine".
- I use Python 3.12 because Databricks serverless runs 3.12.3.

### How my terminal finds commands
- The shell looks for programs in the folders on `PATH`. `~/.zprofile` is read when a shell
  *starts*, so a terminal tab opened before I edited it did not know about Homebrew.
- Homebrew has no prebuilt packages for older macOS versions, so it tried to compile `uv` from
  source (hours). Checking how a tool is distributed and using the vendor installer fixed it.

## Phase 1: repo skeleton

### Pre-commit and CI do different jobs
- **Pre-commit** gives fast feedback on my own machine before a commit exists. But hooks live in
  `.git/hooks/` on my Mac only, can be skipped with `--no-verify`, never run for edits made in
  GitHub's web editor, and only see the files being committed.
- **CI** runs on every push, whoever pushed and however, on a clean machine, and also runs the
  slower tests. Hooks protect me, CI protects the repo.
- My hooks run through `uv run`, so ruff's version comes only from `uv.lock`. Pinning it in two
  places (hook config and lockfile) lets them drift, so a commit could pass locally and fail in CI.

### `pyproject.toml` is the wish list, `uv.lock` is the receipt
- `pyproject.toml` says "I want requests 2 or newer". `uv.lock` records the exact version and
  checksum of every package that was actually tested.
- `uv sync --locked` in CI fails if the two disagree, and changes nothing. Plain `uv sync` would
  quietly re-resolve and rewrite the lockfile. I saw this happen in a throwaway copy.
- Fix for a failure: `uv lock` and commit `uv.lock`. Better habit: `uv add <pkg>` keeps both in sync.

### Keeping data out of Git
- Two layers: `.gitignore` (patterns such as `data/`, `*.csv`) and the pre-commit
  `check-added-large-files` hook (500 KB). A file renamed to `.txt` slips past the first, but the
  size hook blocks it at `git commit`. CI only detects it afterwards, and GitHub rejects files over
  100 MB as a last backstop.
- Git keeps every version forever, so a big file committed once stays in history. Code lives in
  Git, data lives in the lakehouse (a Unity Catalog volume).

### Testing the guards
- A check that has never failed is not proven. I planted an unused import, messy SQL, a fake
  private-key header and a 1 MB file, watched each hook reject them, then deleted them.

## Phase 2: bronze and the data

### Profile the data before designing anything
- Scanning the real PaySim file changed three decisions: it is perfectly clean (so I must inject my
  own defects), its volume is very uneven (so the time anchor moved to 20 Aug), and its built-in
  fraud flag catches only 16 of 8,213 fraud rows (so the derived approval rate is nearly 100%).
- Two independent tools (a Python scan and Databricks SQL) gave the same counts. That is a
  reconciliation.

### What a landing zone and bronze are for
- The landing volume holds files exactly as received. Bronze is raw, append-only Delta that can be
  replayed. If silver has a bug, fix it and rebuild from bronze without refetching the sources.
- Free Edition runs only serverless compute, allows one active pipeline per type, and restricts
  outbound internet to trusted domains. I tested that gov.uk is reachable before designing around it.

### A deterministic generator makes everything else testable
- Seeding each day from (seed, date) means regenerating a day gives identical files. That makes a
  rerun idempotent and lets tests assert exact counts.
- Files are written to a temp name and then renamed, so a half-written file is never picked up.
- Injecting defects onto disjoint rows, plus a manifest of what was injected, makes data quality
  provable: silver's quarantine count has to equal the manifest.
- A deliberate schema change (a new `channel` field from 28 Sep) is how I test schema evolution.

### Asset Bundles
- A bundle is YAML saying which resources should exist (a pipeline, a job) plus the code they run.
  `databricks bundle deploy` makes the workspace match the files and remembers what it created.
  The `dev` target prefixes names with `[dev me]`, so experiments cannot collide with production.
- `bundle validate` only checks the structure. My first deploy created the job but the server
  rejected the pipeline (a `**/*.py` glob is not allowed, plain `**` is). Deploying again was safe
  because a deploy is idempotent.

### Auto Loader and streaming tables
- Auto Loader keeps a checkpoint of which files it has already read, so each update reads only
  new files. A streaming table is append-only: it adds rows and never rewrites old ones.
- The first update ingested everything that was already there (6.36M PaySim rows in about 21 s).
- An explicit schema is the data contract. A value that does not fit lands in `_rescued_data`,
  and a warn-only expectation counts those rescues without dropping rows.
- The pipeline event log records each update's states and the expectation metrics (passed and
  failed counts), which is where a runbook starts.

### Incremental, idempotent and exactly once
- Run A had no new files and added 0 rows in 25 s; PaySim's 493 MB flow finished in under a second
  because the checkpoint already knew that file. Run B read only the three new files.
- A late file (25 Sep, delivered after 26 and 27 Sep) is ingested normally, because Auto Loader goes
  by arrival and not by file name or date.
- Even though an update was interrupted, every day has exactly 40,400 rows: no duplicates.

### Schema evolution
- When the new `channel` column appeared, Auto Loader stopped the update and the platform started a
  new one itself (cause `SCHEMA_CHANGE`). Old rows keep an empty `channel`. A value that conflicts
  with a declared type goes to `_rescued_data` instead.
- A job that runs the pipeline could report "cancelled" for that first update even though the data
  completed, so a job needs a retry (Phase 5).
- I predicted the update would stop and need a manual rerun. Half right: it stopped, but it restarted
  itself. Writing a prediction down first made the difference obvious.

### Reconciliation
- 18 SQL checks compare every bronze table with the files that fed it and with the generator's
  manifests. Counts are not enough, so I also checked values: the numbers written in scientific
  notation parsed to exactly the right amounts.

### Testing the tests
- Passing tests can still be weak. I broke the generator on purpose five ways in a scratch copy and
  confirmed each break was caught by exactly the test meant to catch it.

## Phase 3: silver

### Silver, quarantine and expectations
- Bronze is raw. Silver has one schema for every source, the right types, and one row per event.
- A **quarantine table** keeps every rejected row with a reason code and its original values.
  `ON VIOLATION DROP ROW` would lose them silently. The cost is real, though: 17 fraud events sat in
  quarantine because they also had a defect, so quarantine can hide important rows and needs monitoring.
- An expectation can warn (measure only), drop the row, or fail the update. I used `FAIL UPDATE` for
  invariants that must never reach gold, as a safety net behind the split. There are 21 expectations
  and all passed with 0 failures.

### Deduplication with Auto CDC
- An Auto CDC (SCD Type 1) upsert keyed on `event_id` makes a duplicated or replayed event update the
  same row, so it is idempotent and needs no unbounded streaming state. The price is that the table
  is no longer append-only.

### Proving it
- Predict, then verify: every prediction was exact (6,916,460 silver rows, 6,160 quarantined, 5,600
  duplicates removed, 280 holidays, and +39,560 / +440 for a new day).
- A **content fingerprint** (a hash over every business column of every row) shows "nothing changed"
  better than a row count. A rerun with nothing new left all 8 tables identical, and refreshing one
  table changed only that table. Hash functions skip NULLs, and summing 64-bit hashes overflows in strict
  arithmetic mode, so I summed them as wide decimals.
- Every rule in silver has a reason code that reconciles with the manifests, so quality is proven
  to the row and not only claimed.

### Small lessons
- sqlfluff's Databricks dialect parses Lakeflow SQL. Two lines cannot comply by nature (a long
  schema string, a deliberate `SELECT *`), so they carry an explained `noqa`.
- A validate-only update checks the SQL without processing data, which is the cheap way to find mistakes.
- `to_json` drops null fields by default. A quarantine row should show *which* field was null.

## Phase 4: gold

### Star schema and SCD Type 2
- A **fact table** holds events and measures plus keys; **dimensions** hold the descriptive context
  (who, when, what type). BI tools join them for labels.
- **SCD Type 2** keeps history: a customer who changed segment is several rows, each valid over a
  range. The fact stores the key of the version valid **at the event time** (a point-in-time join).
  Ranges are half-open, `[from, to)`, so the boundary neither gaps nor double counts.
- The **Unknown member** (key -1) keeps facts whose IDs are not in the dimension. An inner join
  would drop them, and a null key would be ambiguous.
- **Surrogate keys as hashes** stay the same when a table is rebuilt, and work in a materialized
  view, which cannot use identity columns.

### Late-arriving changes
- A change event dated earlier than transactions already loaded re-cuts the customer's history in the
  middle, and the fact (a materialized view) re-points only the affected rows. I computed the
  expected number from the before-state (173 sender keys, 98 recipient keys) and it was exact, with the
  other 6,955,610 rows unchanged.

### A reconciliation that fails the run
- Control totals compare gold with bronze, and every check is a `FAIL UPDATE` expectation. A mismatch
  fails the update and names the check, and the error even prints all the totals.
- I did not trust it until it had failed: I made one constraint demand `bronze + 1`, watched the update
  fail with `paysim_rows_match` named, reverted, and rerun to green.
- The trade-off: other gold tables refreshed earlier in the same update stay visible. A
  write-audit-publish pattern would hide them, at the cost of a staging copy.
- An independent check recomputes each customer's segment at event time from the raw change events,
  without Auto CDC, so it cannot share a bug with the thing it checks.

### Rules are measured, not trusted
- Each simple rule has a precision (right when it flags) and a recall (share of fraud it finds).
  PaySim's own rule has 100% precision but 0.19% recall; balance drain has 97.6% recall, but that is a
  simulation artifact, and the realistic night-time rule is wrong more than half the time (44%).

### Layout: measure, do not guess
- I built three scratch copies of the fact (plain, partitioned by date, liquid-clustered) and read the
  files and bytes each query read from `system.query.history`. Partitioning won only a single-date query
  (1 file, 0.4 MB) and was worst for the others: a type-only query opened 47 files (25 MB) against 4 files
  (3.3 MB) when clustered. Many small files cost more than they save.
- At this size the wall-clock time was the same for every layout, so the effect is bytes read, not speed,
  and my plain baseline was not random. State the caveats along with the result.

### Small lessons
- Expectation metrics for materialized views are in the event log, readable with `event_log()` in
  SQL, but a REST events call missed them. `DESCRIBE DETAIL` does not work on a materialized view.
- Every gold view is recomputed in full on each update at this size.
- My layout-experiment script crashed when the query history returned no rows yet, and its cleanup was the
  last line, so the scratch schema was left behind. Cleanup belongs in a `finally` block.
- A new kind of file (a late file with no manifest) exposed a gap in my own reconciliation. I fixed it by
  checking the late file against the landing folder, not by loosening the check.

## Phase 5: automation

### A job is a schedule plus an ordered set of tasks
- A **job** is a set of **tasks** with `depends_on` links, so the order is written down and enforced.
  Mine: generate the day's files and pull the holidays (side by side, because neither needs the
  other), then refresh the pipeline, then verify. The pipeline cannot start before the files exist,
  and nothing says "success" before the checks have run.
- The checks live in their own last task on purpose. The pipeline's own constraints stop one update;
  `verify` re-checks every layer from the landing files, and its failure is what fails the job.

### Idempotent means a second run changes nothing, and I should prove it
- Each step has its own reason: the generator skips a file whose bytes already match, the holiday
  file's name is a hash of its content, Auto Loader remembers the files it read, silver is an upsert on
  `event_id`, gold is recomputed. Together, a rerun is always safe, so the runbook never has to say
  "check first".
- The proof: I re-ran a day that had already landed, and 18 of 19 tables had identical row counts and
  content hashes. The 19th, `dim_date`, changed because I had extended it on purpose. I also hashed
  three cloud-written files against my laptop's copies and they matched exactly.
- **Predict, then verify.** I wrote the expected row counts before the new-day run and 8 of 8 were
  exact. I was wrong about one thing (see below), and that was the useful part.

### Alerts: on failure, tested, and without my address in Git
- Alert on failure and on "too slow", not on success, or the signal drowns. A job-level email is sent
  when the run ends, after the retries, not for each failed attempt.
- The recipient is a bundle variable that defaults to whoever deploys, so no address is committed.
- **An alert I have not tested is a guess.** I broke one check on purpose, watched `verify` fail with
  the check named, and the account owner confirmed the email arrived. Then I reverted (hash identical
  to the commit) and redeployed.

### Retries: explicit ones, and the ones the platform adds
- I set one retry after two minutes on the pipeline task, for a transient platform failure.
- I did not set one on `verify`, yet it ran twice: serverless task compute retries a failed task by
  itself (auto-optimization). That is only safe because every task is idempotent. For a real data
  problem a retry just costs a minute.

### Repair run or run now
- A **repair run** continues a failed run and re-runs only what failed. After my deliberate failure it
  re-ran `verify` alone, in about a minute, and the same run turned green. A fresh run costs about 8
  minutes and does not change the answer.

### Which day is "yesterday"?
- A job needs to know which day to process. If "yesterday" is computed from the wall clock, a task
  that runs again later gets a different day. Airflow solves this with a logical date. Databricks Jobs
  have none, so I pass the run's start date into the task and measure "yesterday" from it.
- I have not yet observed a repair across midnight, so I wrote that down as intent and not as fact.
- Job parameters are not pushed into a Python-wheel task that takes a list of arguments, so I
  reference the parameter explicitly. I read that in the docs instead of guessing.

### Deploying is destructive on purpose
- `bundle deploy` makes the workspace match the repository. It created the new job and deleted the old
  one, because I had removed its file. That is the point of infrastructure as code, and why I asked
  before deploying a job with a live schedule.
- The `prod` target is defined and validated but not deployed, because Free Edition has one workspace
  and one catalog. I can say why, and what a real one would change.

### A schema change inside a job
- I predicted (about 60% sure) that the first pipeline update would fail on the new `device_type`
  column and the job retry would fix it. I was wrong. The platform cancelled the update itself, started
  a follow-up update with cause `SCHEMA_CHANGE`, and the job task simply waited for it: success on the
  first attempt after 484 s instead of 292 s. The retry was not needed.
- Bronze took the new column and silver and gold ignored it, which is the design: bronze never loses
  data, and the layers with a contract change only by review.

### Time bombs
- My calendar table only covered 2026. A one-off pipeline would never notice, but a job that runs
  every day fails on 1 January 2027 and keeps failing. A fixed range in a scheduled job is a bug with
  a date on it. I found it by asking "what happens when this runs for a year", and a strict check
  would have made it loud instead of silent. The same limit now sits at the end of 2028, written in
  the runbook.

### Small lessons
- A failed multi-task run is reported by the API as `INTERNAL_ERROR` with result `FAILED`; the UI says
  Failed.
- My failure message said `expected 1097.00`, because SQL widened a count to a decimal. The line a
  person reads at 6am should be clean, so I fixed it and added a test.
- I nearly wrote "a cancelled run is not a failure" in the runbook. The docs say the opposite by
  default. When I cannot test a claim, I check the docs before it goes in a runbook.
- My helper scripts run on the system Python 3.9, which has no `datetime.UTC`. `DESCRIBE` cannot be a
  subquery, so I read column lists from `information_schema` instead.
