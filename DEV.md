# MJJ development guide

This is the engineering entry point for mojojojo-agent. User installation and
daily commands stay in [README.md](README.md); detailed behavior lives under
[docs/](docs/README.md).

## Local setup

```bash
uv sync
uv run pytest -q
uv run mjj tools
uv build
```

The base wheel is third-party-dependency-free on Python 3.11+. The development
group intentionally installs pytest, prompt-toolkit, and Pillow so both lean and
full paths are tested. `tests/test_minimal_runtime.py` launches Python with
`-S` to prove modern-Python startup without site packages.

## Architecture

The Python control plane owns provider I/O, sessions, permissions, and portable
process behavior. Mojo owns bounded compute hot paths where it produces a
measured win and retains a guarded fallback.

| area | owner |
| --- | --- |
| agent loop and transcript | `mjj/agent.py`, `mjj/session.py` |
| provider requests and usage | `mjj/model.py` |
| automatic model aliases | `mjj/model_routes.py` |
| adaptive KV-cache hints | `mjj/prompt_cache.py` |
| token-bounded tools | `mjj/tools/`, `mjj/ledger.py` |
| hybrid search | `mjj/search/`, `mjj/search/embed.mojo` |
| optional native execution | `mjj/kernels/`, mojosub |
| terminal app | `mjj/tui.py` |

Read [architecture](docs/architecture.md), [native runtime](docs/native-runtime.md),
and [execution](docs/exec.md) before changing a provider or native boundary.

## Mojo build

The repository pins the Mojo toolchain used by the native search ABI. Compile
it locally with:

```bash
pixi run mojo-check
```

`.github/workflows/mojo.yml` performs this build on every push and pull request.
The ordinary Python matrix separately proves that no Mojo compiler or native
library is required for correctness.

## Validation

Run the focused test first, then the whole offline suite. Provider tests use
fake credentials and local SSE streams; ordinary CI must not require secrets.

```bash
uv run pytest -q tests/test_model.py tests/test_prompt_cache.py
uv run pytest -q
uv build
git diff --check
```

Release CI freezes the `full` extra into one executable, excludes test/build
packages from the module graph, smoke-tests core tools, packages per platform,
and exercises both installers against locally built archives.

## Benchmarks and evals

Never publish a number that cannot be reproduced. Benchmarks run under one
lock; evals score task success, token use, cache reads, tool calls, and latency.

```bash
bench/run.sh
uv run python evals/run.py
uv run python bench/search_bench.py
uv run python bench/allocation_bench.py --corpus /path/to/pinned-corpus
bench/flamegraph.sh /path/to/pinned-corpus build/profiles/search-flamegraph.svg
uvx memray==1.19.3 run --trace-python-allocators \
  -o build/profiles/search-allocations.bin \
  bench/allocation_bench.py --iterations 100 --workload-only
uvx memray==1.19.3 stats build/profiles/search-allocations.bin
uvx memray==1.19.3 flamegraph -o build/profiles/search-allocations.html \
  build/profiles/search-allocations.bin
```

Use the same immutable corpus for before/after runs. The allocation report
prints its content SHA-256 plus file and chunk counts; results with different
digests are not comparable. The flame-graph wrapper writes through a temporary
file and accepts output only when the current profiler run produced a complete
SVG.

The detailed search methodology is in [docs/search.md](docs/search.md), visual
measurements in [docs/visualizers.md](docs/visualizers.md), and reference-agent
comparisons in [docs/reference-harness-audit.md](docs/reference-harness-audit.md).

## Working agreements

- Keep model prompts and tool schemas lean.
- Bound every tool result through the ledger.
- Preserve local fallbacks when adding native acceleration.
- Treat model maps as policy tiers, not permanent price claims.
- Test Windows path/process behavior as well as Linux.
- Preserve unrelated changes in a dirty worktree.

See [AGENTS.md](AGENTS.md) for the complete repository contract and
[CONTRIBUTING.md](CONTRIBUTING.md) for contribution mechanics.
