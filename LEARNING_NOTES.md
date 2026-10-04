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

### Testing the tests
- Passing tests can still be weak. I broke the generator on purpose five ways in a scratch copy and
  confirmed each break was caught by exactly the test meant to catch it.
