# mojojojo-agent

Python-hosted coding agent with optional Mojo hot paths. It ships as the
`mojojojo-agent` package, the `mjj` CLI, and the backend for
[mojojojo.app.nz](https://mojojojo.app.nz).

## Product contract

1. **Optimize completed work per token.** Route every tool result through
   `mjj/ledger.py`; output must be bounded, structured, deduplicated, and
   recoverable when clipped.
2. **Never wait for native compilation.** Run the Python fallback immediately
   and adopt a completed mojosub build later.
3. **Measure claims.** Numbers in docs or code need a reproducer under `bench/`
   or `evals/`, including regressions as well as wins.
4. **Degrade cleanly.** Missing Mojo, native libraries, language servers,
   credentials, or network access may reduce capability, never break the base
   harness. Keep this covered by tests.

## Find the code

| concern | paths |
| --- | --- |
| turn loop and prompts | `mjj/agent.py`, `mjj/prompt.py` |
| providers, auth, routing, cache | `mjj/model.py`, `mjj/auth.py`, `mjj/model_routes.py`, `mjj/prompt_cache.py` |
| sessions, goals, plans, delegation | `mjj/session.py`, `mjj/goals.py`, `mjj/subagents.py` |
| token and tool boundary | `mjj/ledger.py`, `mjj/tools/` |
| instructions, skills, plugins, MCP | `mjj/project_docs.py`, `mjj/skills.py`, `mjj/agent_plugins.py`, `mjj/plugins.py`, `mjj/mcp.py` |
| search and repository map | `mjj/search/`, `mjj/repo_map.py` |
| edits, syntax, LSP, undo | `mjj/tools/patch.py`, `mjj/syntax.py`, `mjj/hygiene.py`, `mjj/lsp.py`, `mjj/checkpoints.py` |
| local/remote execution | `mjj/exec/`, `mjj/kernels/` |
| CLI, TUI, hosted server | `mjj/cli.py`, `mjj/tui.py`, `mjj/server.py` |

## Boundaries

- Reuse an existing Codex/ChatGPT credential; do not add a competing login
  flow. Never print tokens or overwrite another harness's auth cache unless the
  operator explicitly enables the existing write-back path. See `mjj/auth.py`
  and `tests/test_auth.py` for the contract.
- Keep the hosted workspace, billing, and no-compiler sandbox boundaries intact.
  Shared app.nz behavior must match `../app-site` and `../mojojojo/auth.go`.
- Treat `../mojosub`, `../mojo-embed`, and `../mojojojo` as optional peers.
  The ordinary Python suite must work without them.

## Reference harnesses

Use other agents as evidence, not as a feature checklist. Start with the pinned
source map and adoption test in
[docs/reference-harness-audit.md](docs/reference-harness-audit.md). Prefer a
small MJJ-native mechanism when it lowers task tokens or failure risk; do not
vendor reference code or add a daemon/dependency without measured justification.

## Validate

```bash
uv run pytest -q                     # complete offline suite
uv build                             # package changes
pixi run mojo-check                  # native search or ABI changes
bench/run.sh                         # published performance claims
```

Run focused tests first. Provider tests use fake credentials and local streams;
CI must not require secrets. See [DEV.md](DEV.md) for subsystem commands and
[CONTRIBUTING.md](CONTRIBUTING.md) for submission guidance.

## Git workflow

- Work on `main` unless the user requests isolation.
- Preserve unrelated worktree changes. Commit coherent, tested increments and
  push `main` directly; routine work does not use pull requests.
- After pushing, leave the checkout on `main` and report uncommitted files.
