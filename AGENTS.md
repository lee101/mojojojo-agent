# mojojojo-agent — working notes for agents

Open-source coding agent harness. Python host, Mojo hot paths. Ships as
`mojojojo-agent` on PyPI with the `mjj` CLI, and as the agent backend behind
[mojojojo.app.nz](https://mojojojo.app.nz).

Design goal, in one line: **the fewest tokens per unit of work of any harness**,
because the expensive parts (search, code execution, diff matching) run native
instead of being paid for in context.

## Non-negotiables

1. **Token efficiency is the product.** Every tool result passes through
   `mjj/ledger.py`. No tool may emit unbounded output. Prefer structured,
   deduplicated, line-anchored output over raw dumps.
2. **Never block the loop on a compile.** mojosub semantics: run CPython now,
   swap to native when the build lands. Same rule everywhere in this repo.
3. **No fabricated benchmarks.** Every number in a README or docstring is
   reproducible from `bench/`. Losses get published next to wins.
4. **Degrade, never crash.** No Mojo toolchain, no mojo-embed `.so`, no network
   — the harness still works, just slower. Guarded by tests.

## Layout

| path | what |
| --- | --- |
| `mjj/auth.py` | OpenAI Max Plan (ChatGPT OAuth) + API key credentials |
| `mjj/model.py` | Responses API streaming client, usage ledger |
| `mjj/agent.py` | the turn loop, tool dispatch, interrupts |
| `mjj/session.py` | rollout JSONL, resume, fork |
| `mjj/ledger.py` | token accounting + output truncation policy |
| `mjj/config.py` | config resolution (env, `~/.mjj/config.toml`, flags) |
| `mjj/project_docs.py` | bounded root-to-cwd `AGENTS.md` discovery |
| `mjj/tools/` | shell, apply_patch, read/ls, py, search, skill loading |
| `mjj/skills.py` | scoped `SKILL.md` discovery and metadata |
| `mjj/syntax.py` | exact parsers + optional per-language tree-sitter checks |
| `mjj/visualize.py` | token-free native WebGL visualizer expansion |
| `mjj/search/` | mojo-embed backed index: literal + lexical + semantic |
| `mjj/repo_map.py` | reference-ranked, budget-fitted repository symbol map |
| `mjj/checkpoints.py` | secure external patch snapshots and conflict-safe undo |
| `mjj/lsp.py` | stdio client for already-installed language servers |
| `mjj/terminal_images.py` | TTY-safe Kitty/ANSI image presentation boundary |
| `mjj/kernels/` | mojosub `@jit` hot paths with CPython fallbacks |
| `mjj/server.py` | SSE agent backend with app.nz SSO + credit billing |
| `mjj/cli.py` | `mjj`, `mjj exec` (headless, scriptable) |
| `evals/` | real-project construction + Python→Mojo port evals |

## Credentials — OpenAI Max Plan

The machine already holds a signed-in ChatGPT max-plan credential. Reuse it,
never re-implement device login as the primary path.

- File: `$CODEX_HOME/auth.json`, default `~/.codexinfinity/auth.json`
  (`MJJ_CODEX_HOME` overrides; `~/.codex/auth.json` is the fallback).
- Shape: `{"auth_mode": "chatgpt", "tokens": {id_token, access_token,
  refresh_token, account_id}, "last_refresh": iso8601}`.
- Issuer `https://auth.openai.com`, token endpoint `/oauth/token`,
  client id `app_EMoamEEZ73f0CkXaXp7hrann`.
- Refresh: JSON POST `{client_id, grant_type: "refresh_token", refresh_token}`.
- Then exchange the fresh `id_token` for a usable API key: form POST with
  `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`,
  `requested_token=openai-api-key`,
  `subject_token_type=urn:ietf:params:oauth:token-type:id_token`.
  The returned `access_token` **is** the API key.
- Refresh proactively every 4h and reactively on 401. Never write the harness's
  own tokens back over `~/.codexinfinity/auth.json` unless
  `MJJ_WRITE_BACK_AUTH=1` — codex-infinity owns that file.

Reference implementation to mirror (Go):
`/nvme0n1-disk/code/openpaths/internal/handler/openai_max_plan.go`.

## The stack this harness is built on

- **mojosub** (`../mojosub`) — compiles a subset of Python to Mojo, content
  addressed cache, ~1.2 us per hot call. Requires `source=` when the code was
  `exec`'d from an AST, and `MODULAR_HOME` matching the resolved `mojo` binary.
- **mojo-embed** (`../mojo-embed`) — int8 SIMD flat vector index, C ABI in
  `src/embed/capi.mojo`, prebuilt `build/libmojo_embed.so`. No persistence and
  no embedding model upstream; both live here.
- **mojojojo** (`../mojojojo`) — the execution service. Local jail at
  `/usr/local/bin/mojojail`, worker on `:4342`, web tier on `:4341`, remote at
  `https://mojojojo.app.nz` with `mj_live_` keys. **No compiler inside the
  sandbox** — that boundary is load-bearing, do not weaken it.

## Reference harnesses (read, do not vendor)

- `~/code/codex/codex-rs` — apply-patch format, `execpolicy`, rollout/session
  model, exec-server. Note upstream URLs are scrubbed to `https://n`.
- `~/code/grok-infinity` — Grok Build TUI, autonomous continuation modes.
- `../app-site` — app.nz SSO, credits ledger, site conventions.
- `../mojojojo/auth.go` — the exact cookie names and ledger semantics to copy.

## app.nz integration

Shared cookies `__Host-appnz_sso_session` and `appnz_session` on `.app.nz`.
Keys live in the shared `api_keys` table with an `app_id`. Agent runs bill the
same ledger as `exec:` runs do; the app_id for this service is `mojojojo`.

## Commands

```bash
uv sync                              # dev env
uv run pytest -q                     # unit tests (offline, no creds needed)
uv run mjj exec "..."                # headless run
uv run mjj search QUERY [PATH]        # hybrid disk search
uv run mjj index                      # build or refresh a repo index
uv run mjj visualize demo --kind cells # scaffold standalone WebGL
bench/run.sh                         # all benchmarks under a lock
```

## Git workflow

- Work directly on `main` unless the user explicitly asks for an isolated
  branch or worktree.
- Commit tested, coherent increments and push `main` directly. Do not open
  pull requests for routine work in this repository.
- Before staging, inspect the complete worktree and preserve unrelated user
  changes. After pushing, leave the checkout on `main` and report any
  remaining uncommitted files.
