# Developing mojojojo-agent

Use this when you are changing the harness itself. Daily install and usage stay
in [README.md](README.md); subsystem detail is in [DEV.md](DEV.md) and
[docs/](docs/README.md).

## Run the source, not the frozen binary

`~/.local/bin/mjj` is often a release binary. Editing this repository does not
change that binary until you reinstall or rebuild it. For dogfooding and
self-fixes:

```bash
cd /path/to/mojojojo-agent
uv sync
uv run mjj --version   # must match mjj/version.py
uv run mjj             # interactive, loads this checkout
```

Confirm you are on the checkout:

```bash
uv run python -c "import mjj, pathlib; print(mjj.__version__, pathlib.Path(mjj.__file__).resolve())"
```

If `which mjj` still points at a frozen ELF, prefer `uv run mjj` or put the
editable entry ahead of `~/.local/bin` on `PATH`.

## Persist defaults

Interactive `/model`, `/provider`, `/reasoning` (`/effort`), and `/verbosity`
write into `$MJJ_HOME/config.toml` (default `~/.mjj/config.toml`). Hotkey cycles
do the same. Inspect with:

```bash
uv run mjj config
```

Precedence is built-ins → user config → project `.mjj/config.toml` → `MJJ_*`
env → CLI flags. See [docs/config.md](docs/config.md).

## Self-edit loop

1. Start from the repo: `uv run mjj`
2. Let the agent change Python under `mjj/` and tests under `tests/`.
3. Validate with the focused suite, then the offline suite:

```bash
uv run pytest -q tests/test_config.py tests/test_tui.py tests/test_auth.py
uv run pytest -q
```

4. Restart the interactive process so it loads the new modules. There is no
   hot-reload of the running agent process; `/reload` only refreshes tools and
   skills.
5. Optional native search/ABI changes: `pixi run mojo-check`.
6. Package check: `uv build`.

When the agent is editing itself, keep permission mode `auto` or `ask`, run
tests after patches, and do not expect the frozen `mjj` on `PATH` to pick up
source edits.

## Rebuild / reinstall a release binary

After a coherent, tested change set:

```bash
uv build
# then either:
uv tool install --force --reinstall 'mojojojo-agent[full]'
# or rebuild/publish the frozen installer assets used by install/install.sh
```

The published installers replace `~/.local/bin/mjj` atomically after checksum
and smoke checks. Until that happens, `uv run mjj` remains the source of truth
for local development.

## Where to look

| concern | path |
| --- | --- |
| turn loop / prompts | `mjj/agent.py`, `mjj/prompt.py` |
| auth / providers / routing | `mjj/auth.py`, `mjj/model.py`, `mjj/model_routes.py` |
| config persistence | `mjj/config.py`, `mjj/tui.py` |
| tools / ledger | `mjj/tools/`, `mjj/ledger.py` |
| CLI / TUI | `mjj/cli.py`, `mjj/tui.py` |
| always-loaded agent rules | `AGENTS.md` (4 KiB cap) |

Product contracts and contribution mechanics: [AGENTS.md](AGENTS.md),
[CONTRIBUTING.md](CONTRIBUTING.md). Coding-workflow parity notes:
[docs/pi-parity.md](docs/pi-parity.md).
