# mojojojo-agent

A coding agent harness that spends tokens like they cost money.

[Website](https://mojojojo.cc/agent) ·
[Documentation](docs/README.md) ·
[Releases](https://github.com/lee101/mojojojo-agent/releases) ·
[Issues](https://github.com/lee101/mojojojo-agent/issues) ·
[MIT licence](LICENSE)

Same job as Codex or Claude Code: read a repository, change it, run the tests,
report back. The difference is where the work happens. Searching a codebase,
executing code, and matching diffs are **computation**, not reasoning — so this
harness does them natively (Mojo, via [mojosub][] and [mojo-embed][]) and sends
the model an answer instead of a pile of context to read.

Linux and macOS can install the self-contained release binary without Python:

```bash
curl -fsSL https://mojojojo.cc/install.sh | sh
mjj                                  # opens the interactive coding agent
mjj auth --probe                     # reuses your existing ChatGPT sign-in
mjj exec "make the failing test in tests/test_router.py pass"
```

On Windows PowerShell:

```powershell
irm https://mojojojo.cc/install.ps1 | iex
```

The installers select the operating system and CPU, verify the release's
SHA-256 checksum, and install into a user-owned directory. Set
`MJJ_INSTALL_DIR` to choose another location or `MJJ_VERSION=v0.3.0` to pin a
release. The binaries need no system Python. Mojo acceleration remains a
guarded optional backend, so the agent and hybrid search still work when a
compatible Mojo toolchain or `mojo-embed` library is not installed. Linux
artifacts are built on Ubuntu 22.04 for a stable glibc baseline.

The Python package remains available too:

```bash
uv tool install mojojojo-agent
```

From a checkout, `uv sync && uv run pytest -q` creates the development
environment and verifies it. The base package uses Pillow for bounded vision
inputs and prompt-toolkit for the cross-platform composer; `mojosub` and
`mojo-embed` are guarded accelerators, not startup requirements.

New users should start with the [getting-started guide](docs/getting-started.md).
The [documentation index](docs/README.md) links task-oriented guides, internals,
deployment, and the Pi Infinity parity notes.

Status: **early but working end to end** — credential, model client, loop,
tools, search, sandboxed execution, and a served mode with app.nz sign-in.

## Interactive agent

Running `mjj` with no arguments opens the cross-platform terminal app. Type `/`
for the searchable command palette; Tab completes both commands and their valid
values. `/model` shows numbered shortcuts with the active model marked, while
`/model 2`, `/model terra`, and `/model next` make switching quick. Custom model
IDs remain accepted. F2/Alt+M cycles provider-specific models, F3/Alt+R cycles
reasoning, F4/Alt+V cycles verbosity, and Alt+Enter inserts a newline. The
original empty-composer left/right reasoning and Shift+Up/Down model controls
remain available. `/help`, `/commands`, `/hotkeys`, and `/keys` expose the live
surface. Long headless turns emit a heartbeat on stderr while preserving the
final answer alone on stdout.

`/image PATH` now previews the attachment as well as queueing it; `/preview PATH`
displays without attaching. The model can call `display_image` after creating or
transforming an asset, so the image appears at that exact point in the response
chain. Kitty terminals use native `kitten icat`; other color terminals receive a
small true-color half-block preview. Redirected and headless output never
contains terminal graphics escapes or image bytes.

Type `@` to fuzzy-complete a repository file; text is attached within a strict
64 KiB total budget, `@path:10-40` selects a line range, and image mentions use
the same quality-85 WebP vision path as `--image`. `!command` runs a local shell
command and keeps its bounded result as context; `!!command` keeps it local.
`/permissions` switches live between `auto`, `ask`, and `read-only`, while
`/status`, `/diff`, `/review`, and `/init` cover the common repository-control
flow without leaving the app. Every successful patch creates an external,
bounded checkpoint; `/undo` restores the latest one only when none of its files
changed afterward, and `/checkpoints` shows the retained history.

Configured MCP stdio servers contribute bounded namespaced tools shared by the
same loop; `/mcp` shows inventory and startup warnings, while `/reload` refreshes
servers, built-ins, and skills. Multi-step work can maintain a structured
`update_plan` state visible through `/plan`. See [the MCP guide](docs/mcp.md).
Installed language servers also power call hierarchy and semantic rename.
Multi-file rename edits are confined to the workspace, approval-gated,
syntax-checked, atomic, and checkpointed for conflict-safe `/undo`; see the
[LSP refactor guide](docs/lsp-refactors.md).

`/login chatgpt` launches the supported Codex browser sign-in and reuses its
credential cache; `/login device` uses device-code sign-in. `/login openpaths`,
`/login openrouter`, and `/login openai` securely prompt for an API key and save
it under `~/.mjj/auth.json`.

## Providers and images

OpenAI Responses, OpenPaths, OpenRouter, and custom OpenAI-compatible gateways
share one agent loop and tool transcript. `auto` uses an explicitly scoped mjj
OpenAI key first, then prefers `OPENPATHS_API_KEY` when present, then an existing
ChatGPT/Codex sign-in or `OPENAI_API_KEY`. OpenRouter is selected explicitly so
an unrelated exported key cannot silently change providers.

```bash
mjj exec --provider openpaths --model openpaths/auto-code "fix the tests"
mjj exec --provider openrouter --model openrouter/auto "review this repo"
mjj exec --image screenshot.png "match this design in the current app"
mjj exec --permission-mode read-only @src/router.py "review this file"
mjj visualize signal-field --kind aurora --palette ultraviolet --seed 29
```

Images are orientation-corrected, bounded to a 2048-pixel edge, precompressed
in memory as WebP quality 85, and sent to model vision. Their source paths and
dimensions are also exposed to the coding tools, so the agent can copy or read
the actual asset when building a project. See [`visualbench/`](visualbench/) for
the shader gallery, deterministic browser captures, and screenshot health
scores used to test this workflow.

Set `MJJ_IMAGE_PROTOCOL=auto|kitty|ansi|off` to override terminal image
detection. `auto` is TTY-safe and is the default; forcing `kitty` is useful in
other terminals that implement the Kitty graphics protocol.

`mjj visualize` expands a short command into a standalone deterministic WebGL
experience with procedural and image-transform modes. It is an on-demand CLI
primitive used through the existing shell tool, so it adds **zero always-on
tool-schema tokens** to ordinary turns. A bundled visualizer skill teaches the
agent the compact workflow only when visual work calls for it. See the
[visualizer guide](docs/visualizers.md) for modes and measured token expansion.

## Why it is cheaper

| what a harness does | usual approach | here |
| --- | --- | --- |
| find code | dump files into context | ranked `path:line` hits from a native int8 index |
| understand a repository | read every file | reference-ranked symbol map, fitted to budget |
| navigate/refactor symbols | require a fixed IDE stack | installed LSP for calls/rename, hybrid-index fallback for reads |
| display generated images | base64 in text or external viewer | metadata-only event; native Kitty or bounded ANSI UI |
| run code | shell out to CPython | subset-compiled to Mojo, cached by content hash |
| edit code | rewrite the file | atomic `apply_patch`, syntax gate, checkpoint, `+n/-n` summary |
| validate code | run every build inline | parser checks now, optional formatter, compiler jobs in background |
| tool output | truncate at N bytes | one ledger, head+tail kept, exactly what was dropped is stated |
| reasoning | re-derived each turn | reasoning items echoed back verbatim so the cache hits |
| long sessions | resend an ever-growing transcript | server compaction replaces old items with opaque carried state |

### Search, against ripgrep

Corpus is a real 270-file repository. `bench/search_bench.py` reproduces it.

| query | mjj tokens | `rg -n` tokens |
| --- | ---: | ---: |
| `errInsufficientCredits` | 56 | 64 |
| `workerBootstrap` | 39 | 42 |
| `mojojail` | 141 | 850 |
| `billed_ms` | 131 | 545 |
| `worker_bootstrap` → finds `workerBootstrap` | 243 | 0 (rg finds nothing; reading the file costs 1685) |

Search that always answers is worse than search that says *no matches*, so a
hit must share a distinctive word with the query. [docs/search.md](docs/search.md)
shows the measurements that forced that design.

Broad orientation is available without a file dump:

```json
{"path": ".", "symbols": true, "query": "authentication refresh"}
```

Clipped tool results remain recoverable from their `.mjj/tool-results/` address,
and nested project instructions are loaded once when a tool first enters their
subtree. Both behaviors add zero tool-schema tokens. The
[reference harness audit](docs/reference-harness-audit.md) records the OpenCode,
Hermes, and Aider designs that motivated these choices.

The same adaptive literal → lexical/mojo-embed path is usable directly from disk:

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

The default OpenAI path is the ChatGPT sign-in you already have — the one
Codex Infinity wrote to `~/.codexinfinity/auth.json` (or Codex wrote to
`~/.codex/auth.json`). `mjj` reads it and refreshes OAuth tokens in memory.
It never overwrites the owning tool's file unless `MJJ_WRITE_BACK_AUTH=1`.
Set `MJJ_OPENAI_API_KEY` to force a plain API key; after repeated Max-plan
authentication failures, `OPENAI_API_KEY` is the automatic fallback.

Long sessions enable Responses API server-side compaction at 200,000 rendered
tokens. Set `MJJ_COMPACT_THRESHOLD=0` to disable it or choose another threshold.
If a backend does not support compaction, the request is retried without it.

```bash
mjj login chatgpt            # supported browser sign-in via Codex
mjj login chatgpt --device   # device-code flow for a headless machine
mjj login openpaths          # securely save an OpenPaths API key
mjj auth                     # providers, plan, and expiry; never secrets
mjj auth --probe             # one real round trip
```

Sessions are append-only JSONL and can be resumed, cloned, branched, named,
imported, or exported as JSONL or self-contained HTML:

```bash
mjj sessions
mjj exec --resume SESSION_ID "continue the implementation"
mjj exec --fork SESSION_ID --name experiment "try the other design"
mjj export transcript.html --session SESSION_ID
mjj import transcript.jsonl
```

Inside `mjj`, use `/history`, `/resume`, `/session`, `/name`, `/clone`,
`/tree`, `/tree ITEM`, `/export`, and `/import`. Server-side context compaction
remains automatic and the full on-disk rollout is retained.

For long-running agent work, `--auto-next-steps` keeps executing concrete next
steps and `--auto-next-idea` selects and begins the highest-impact improvement
after completion. Combine them for the Pi Infinity/Grok-style continuation
cycle; `--auto-max-turns N` bounds synthetic turns (`0` means until interrupted).
Interactive sessions expose the same controls as `/auto MODE [N]`.

```bash
mjj exec --auto-next-steps --auto-next-idea --auto-max-turns 4 \
  "build the feature, validate it, then improve it"
```

Durable goals add an explicit stopping contract that survives sessions and
process restarts. `/goal OBJECTIVE` starts one interactively; `mjj exec --goal
OBJECTIVE` does the same headlessly. Goal checkpoints are bounded and the model
must attach evidence when it marks the objective complete or blocked. Inspect
or control the state without a model call using `mjj goal`, `mjj goal pause`,
`mjj goal resume`, and `mjj goal clear`. See [the goal guide](docs/goals.md).

The agent can also fan out bounded `delegate` work: read-only reviewers inspect
in parallel, while implementation workers operate on isolated snapshots and
return reviewable Git commits without touching the parent checkout. See the
[subagent guide](docs/subagents.md).

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

The coding-workflow feature comparison with the upstream-synced Pi Infinity
tree is maintained in [docs/pi-parity.md](docs/pi-parity.md).

`mjj skills` discovers project and user `SKILL.md` workflows. The model's
bounded `skill` tool loads a workflow only when needed, so full domain manuals
do not tax every turn. Hosted sessions only see workspace skills. See
[docs/skills.md](docs/skills.md).

Like Codex and OpenCode, local runs automatically load bounded instruction
files. Project directories prefer `AGENTS.override.md`, `AGENTS.md`,
`CLAUDE.md`, then deprecated `CONTEXT.md`; deeper files apply later. MJJ also
reuses the first personal rule from MJJ, OpenCode, or Claude config. Hosted
tenants never inherit service-account rules. Configure or disable the 32 KiB
cap with `agent.project_doc_max_bytes`; see [the instruction guide](docs/project-instructions.md).

## Served, with app.nz sign-in

`python -m mjj.server` puts the same loop behind an SSE endpoint that
authenticates with the shared app.nz session cookie or an `mj_live_` key, bills
the same credit ledger the rest of the platform uses, and gives every account
its own workspace. mojojojo.cc proxies it into the editor's agent panel.
See [docs/server.md](docs/server.md).

## Layout

See [AGENTS.md](AGENTS.md) — it is the working contract for this repo, and the
map of which module owns what. [docs/](docs/) covers the tools, search,
execution, the server, and the accelerated kernels.

The project is MIT-licensed and developed in public. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the local workflow and pull-request
expectations, and [docs/architecture.md](docs/architecture.md) for the runtime
and security boundaries.

## Licence

MIT.

[mojosub]: https://github.com/lee101/mojosub
[mojo-embed]: https://github.com/lee101/mojo-embed
