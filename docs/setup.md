# Setup guide

Tested on macOS 14 (Apple Silicon). All tools used are free.

## 1. Tooling

1. **Homebrew** (macOS package manager): follow the installer at <https://brew.sh>, then run the
   two `eval ... shellenv` lines it prints so `brew` is on your PATH.
2. **uv** (Python and virtual environment manager):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zprofile
   uv python install 3.12
   ```
3. **Databricks CLI** (from Databricks' own Homebrew tap):
   ```bash
   brew install databricks/tap/databricks
   ```
4. **Git and the GitHub CLI** (`gh`), then `gh auth login`.

## 2. Databricks workspace

1. Sign up for **Databricks Free Edition** yourself (no card needed) and note your workspace
   URL, which looks like `https://dbc-xxxxxxxx-xxxx.cloud.databricks.com`.
2. Log in from the CLI. A browser window opens and you approve with your normal login:
   ```bash
   databricks auth login --host https://<your-workspace-url> --profile DEFAULT
   ```
3. Check it works: `databricks current-user me`.

The workspace URL and tokens stay in `~/.databrickscfg` and the macOS keychain. Never commit them.

## 3. Project

```bash
git clone https://github.com/Babu3097/payments-fraud-lakehouse.git
cd payments-fraud-lakehouse
uv sync                          # creates .venv with the exact pinned versions
uv run pre-commit install        # lint hooks run on every commit
uv run pytest                    # run unit tests
```

The bundle (`databricks.yml`) uses the CLI profile named `DEFAULT`, so keep that profile name when
you log in (step 2). Check the bundle without deploying anything: `databricks bundle validate`.

Optional: install the Databricks AI Tools skills for Claude Code, scoped to this project:
`databricks aitools install --agents claude-code --scope project`

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `command not found: databricks` in a terminal that was already open | `~/.zprofile` is only read when a shell starts | Open a new terminal tab |
| `brew install uv` starts compiling CMake and Rust | On older macOS versions Homebrew has no prebuilt packages (Tier 3), so it builds from source | Stop it and use the vendor installer shown above |
| `brew install databricks` says no formula | The CLI is not in the main Homebrew catalogue | Use `brew install databricks/tap/databricks` |
