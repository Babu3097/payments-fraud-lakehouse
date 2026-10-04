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
- **Why:** Short-lived tokens that refresh automatically, SSO and MFA apply, and there is no secret
  to copy-paste or leak.
- **Open:** Unattended CI would use a service principal (OAuth machine-to-machine, or OIDC
  federation from GitHub) with least-privilege grants. We still need to check whether Free
  Edition supports service principals. If not, CI stays workspace-free (lint and unit tests only).

## ADR-004: One source of truth for tool versions, same checks in hooks and CI

- **Status:** Accepted
- **Decision:** The pre-commit hooks for ruff and sqlfluff run via `uv run`, so their versions
  come from `uv.lock`. CI runs the same pre-commit hooks, then pytest.
- **Why:** Pinning versions in both a hook config and a lockfile lets them drift, so a commit can
  pass locally and fail in CI. Hooks catch problems in seconds on the laptop. CI is the
  enforcement, because hooks can be skipped with `--no-verify`.
- **Trade-offs:** Hooks need `uv` installed and start slightly slower than isolated hook
  environments.
