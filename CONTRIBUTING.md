# Contributing

Mojo Agent is MIT-licensed. Focused bug fixes, docs, provider compatibility,
fallback hardening, and measured token-efficiency improvements are welcome in
the [public repository](https://github.com/lee101/mojojojo-agent).

## Start locally

Python 3.10+ and [uv](https://docs.astral.sh/uv/) are enough for development:

```bash
git clone https://github.com/lee101/mojojojo-agent.git
cd mojojojo-agent
uv sync
uv run pytest -q
uv run mjj --version
```

Mojo, mojosub, mojo-embed, credentials, and network access are optional. Use
`uv sync --extra syntax` only when working on tree-sitter validation.

## Choose the narrow check

Run the focused check while iterating, then the complete offline suite before
submission.

| change | focused validation |
| --- | --- |
| docs only | `uv run pytest -q tests/test_docs.py` |
| provider, auth, or caching | `uv run pytest -q tests/test_auth.py tests/test_model.py tests/test_prompt_cache.py` |
| tools or agent loop | matching `tests/test_*.py`, then `uv run pytest -q` |
| search or repository map | matching search tests and benchmark; add `pixi run mojo-check` for Mojo/ABI changes |
| packaging or runtime deps | `uv build` and `uv run pytest -q tests/test_minimal_runtime.py` |
| published performance claim | the exact reproducer under `bench/` or `evals/` |

The base harness must still start without optional packages. Tests must use fake
credentials and local streams; never commit keys, auth caches, transcripts,
`.env` files, generated archives, or local benchmark output.

## Propose a harness-inspired change

Read the pinned [reference harness guide](docs/reference-harness-audit.md) before
copying a pattern from Aider, OpenCode, Codex, Grok Build, or Hermes. A useful
proposal states:

1. the coding job or failure it improves;
2. the smallest reference mechanism worth adapting;
3. permanent prompt/schema tokens and bounded result behavior;
4. behavior without optional services or dependencies;
5. a test or benchmark that can reject the idea.

Feature count is not the target. Prefer extending an existing MJJ tool or state
boundary over adding another always-visible tool.

## Documentation style

- Lead with the user outcome and one runnable example.
- Keep one canonical explanation; link to it instead of copying it.
- Put contributor internals in `DEV.md`, dogfooding/self-edit in
  `developers.md`, user behavior in `docs/`, and only always-relevant agent
  constraints in `AGENTS.md`.
- Keep commands copyable, local links valid, and claims reproducible.
- Avoid workstation-specific paths, credential shapes, roadmap speculation,
  and comparison claims that are not tied to a pinned source.

`AGENTS.md` is injected into agent context, so its 4 KiB budget is enforced by
the docs test. Add a rule there only when it changes how most repository tasks
must be performed.

## Pull requests

Keep one coherent outcome per pull request. Explain the user-visible change,
why it belongs in MJJ, and the exact checks run. Preserve Responses items
verbatim between turns, keep tool output behind `mjj/ledger.py`, retain guarded
fallbacks, and never weaken hosted execution boundaries for local convenience.

See [DEV.md](DEV.md) for architecture, native builds, benchmarks, and release
work.
