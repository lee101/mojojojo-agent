# mojojojo-agent

A coding agent harness that spends tokens like they cost money.

Same job as Codex or Claude Code: read a repository, change it, run the tests,
report back. The difference is where the work happens. Searching a codebase,
executing code, and matching diffs are **computation**, not reasoning — so this
harness does them natively (Mojo, via [mojosub][] and [mojo-embed][]) and sends
the model an answer instead of a pile of context to read.

Linux and macOS can install the self-contained release binary without Python:

```bash
curl -fsSL https://mojojojo.app.nz/install.sh | sh
mjj auth --probe                     # reuses your existing ChatGPT max-plan sign-in
mjj exec "make the failing test in tests/test_router.py pass"
```

On Windows PowerShell:

```powershell
irm https://mojojojo.app.nz/install.ps1 | iex
```

The installers select the operating system and CPU, verify the release's
SHA-256 checksum, and install into a user-owned directory. Set
`MJJ_INSTALL_DIR` to choose another location or `MJJ_VERSION=v0.1.0` to pin a
release. The binaries need no system Python. Mojo acceleration remains a
guarded optional backend, so the agent and hybrid search still work when a
compatible Mojo toolchain or `mojo-embed` library is not installed. Linux
artifacts are built on Ubuntu 22.04 for a stable glibc baseline.

The Python package remains available too:

```bash
uv tool install mojojojo-agent
```

From a checkout, `uv sync && uv run pytest -q` creates the development
environment and verifies it. The base package is stdlib-only on Python 3.11+;
`mojosub` and `mojo-embed` are guarded accelerators, not startup requirements.

Status: **early but working end to end** — credential, model client, loop,
tools, search, sandboxed execution, and a served mode with app.nz sign-in.

## Why it is cheaper

| what a harness does | usual approach | here |
| --- | --- | --- |
| find code | dump files into context | ranked `path:line` hits from a native int8 index |
| run code | shell out to CPython | subset-compiled to Mojo, cached by content hash |
| edit code | rewrite the file | `apply_patch` envelope, per-file `+n/-n` summary |
| tool output | truncate at N bytes | one ledger, head+tail kept, exactly what was dropped is stated |
| reasoning | re-derived each turn | reasoning items echoed back verbatim so the cache hits |
| long sessions | resend an ever-growing transcript | server compaction replaces old items with opaque carried state |

### Search, against ripgrep

Corpus is a real 99-file repository. `bench/search_bench.py` reproduces it.

| query | mjj tokens | `rg -n` tokens |
| --- | ---: | ---: |
| `errInsufficientCredits` | 19 | 20 |
| `workerBootstrap` | 39 | 42 |
| `mojojail` | 141 | 850 |
| `billed_ms` | 131 | 545 |
| `worker_bootstrap` → finds `workerBootstrap` | 235 | 0 (rg finds nothing; reading the file costs 1339) |

Search that always answers is worse than search that says *no matches*, so a
hit must share a distinctive word with the query. [docs/search.md](docs/search.md)
shows the measurements that forced that design.

The same fused `rg` + lexical + mojo-embed path is usable directly from disk:

```bash
mjj search workerBootstrap src --stats
mjj search 'def .*handler' --regex --json
mjj index                              # prepare or incrementally refresh .mjj/index
```

### Execution

`docs/exec.md` reproduces this. A hot accelerated kernel is **2.7 ms** where
CPython in-process is 27 ms; the sandbox costs ~215 ms for code that has to be
isolated, and the harness picks the cheapest path that is still safe.

### A real session

`evals/run.py` runs the agent against real repositories and scores what it
*cost*, not just whether it passed:

```
pass build-cli          92.7s   in    25681 (   3840 cached)  out  4424  tools  8
pass fix-failing-test   30.6s   in    14533 (   1792 cached)  out   709  tools  8
pass port-to-mojo      529.8s   in  1439937 ( 1198848 cached)  out 17648  tools 50
```

83% of the long session's input was served from cache — that is what echoing
reasoning verbatim and pinning a session cache key buys.

Every number this project publishes is reproducible from `bench/` or
`evals/`. Losses get published next to wins.

## Credentials

The primary path is the ChatGPT max-plan sign-in you already have — the one
Codex Infinity wrote to `~/.codexinfinity/auth.json` (or Codex wrote to
`~/.codex/auth.json`). `mjj` reads it and refreshes OAuth tokens in memory.
It never overwrites the owning tool's file unless `MJJ_WRITE_BACK_AUTH=1`.
Set `MJJ_OPENAI_API_KEY` to force a plain API key; after repeated Max-plan
authentication failures, `OPENAI_API_KEY` is the automatic fallback.

Long sessions enable Responses API server-side compaction at 200,000 rendered
tokens. Set `MJJ_COMPACT_THRESHOLD=0` to disable it or choose another threshold.
If a backend does not support compaction, the request is retried without it.

```bash
mjj auth            # what credential is in play, what plan, when it expires
mjj auth --probe    # one real round trip
```

Headless runs keep the final answer alone on stdout and send tool progress to
stderr, so shell capture stays clean. `--json` emits JSONL events,
`--ephemeral` skips session persistence, and `-o result.txt` also writes the
last assistant message. A positional prompt and piped stdin are combined with
a bounded `<stdin>` block.

## Configuration and skills

`~/.mjj/config.toml` supplies user defaults; the nearest repository
`.mjj/config.toml` overrides it, then `MJJ_*` environment values and command
flags win. `mjj config` prints the resolved non-secret values. See
[docs/config.md](docs/config.md).

`mjj skills` discovers project and user `SKILL.md` workflows. The model's
bounded `skill` tool loads a workflow only when needed, so full domain manuals
do not tax every turn. Hosted sessions only see workspace skills. See
[docs/skills.md](docs/skills.md).

Like Codex, every run loads at most 32 KiB total from `AGENTS.md` files between
the Git root and working directory. Deeper files apply later, and
`AGENTS.override.md` replaces `AGENTS.md` in the same directory. Configure or
disable the cap with `agent.project_doc_max_bytes`.

## Served, with app.nz sign-in

`python -m mjj.server` puts the same loop behind an SSE endpoint that
authenticates with the shared app.nz session cookie or an `mj_live_` key, bills
the same credit ledger the rest of the platform uses, and gives every account
its own workspace. mojojojo.app.nz proxies it into the editor's agent panel.
See [docs/server.md](docs/server.md).

## Layout

See [AGENTS.md](AGENTS.md) — it is the working contract for this repo, and the
map of which module owns what. [docs/](docs/) covers the tools, search,
execution, the server, and the accelerated kernels.

## Licence

MIT.

[mojosub]: https://github.com/lee101/mojosub
[mojo-embed]: https://github.com/lee101/mojo-embed
