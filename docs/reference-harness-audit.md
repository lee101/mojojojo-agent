# OpenCode, Hermes, and Aider audit

This audit was performed on 2026-08-04 against:

- [OpenCode](https://github.com/anomalyco/opencode) `dev` at `7fe993879f98`;
- [Aider](https://github.com/Aider-AI/aider) `main` at `5dc9490bb35f`;
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) `main` at
  `91937a6dc3ff`, plus the local integration fork at `e826c16a6`.

The goal was not feature-count parity. A feature belongs in MJJ when it improves
completed coding work per token and still degrades cleanly without a daemon,
network service, or optional compiler.

## Adopted

| Reference idea | MJJ implementation | Context cost |
| --- | --- | ---: |
| OpenCode retains full truncated tool output at an address | Every clipped MJJ result is saved mode `0600` under `.mjj/tool-results/`; the bounded result contains a path usable by `read` or `search` | 0 schema tokens |
| Hermes/OpenCode discover compatible project hints | `AGENTS.override.md`, `AGENTS.md`, `CLAUDE.md`, and `CONTEXT.md` use bounded root-to-CWD plus lazy nested scope; local runs reuse one MJJ/OpenCode/Claude global rule | 0 schema tokens |
| Aider ranks tree-sitter tags by cross-file references and mentioned identifiers | `list` with `symbols=true` ranks indexed declarations by cross-file term references and an optional task query, then pre-fits complete file blocks to the list budget | 43 schema tokens |
| OpenCode and Hermes protect edits with external snapshots | Every successful patch stores a secure, bounded checkpoint outside the worktree; undo verifies post-edit hashes and modes before restoring | 79 schema tokens |
| OpenCode exposes installed language servers and semantic edits | `navigate` provides definition, references, hover, symbols, call hierarchy, and checkpointed atomic rename; safe reads keep the hybrid-index fallback | 126 schema tokens |
| OpenCode discovers formatters; Hermes queues background work and steering | `check format=true` is approval-gated and checkpointed; shell jobs return pollable IDs; hosted steering queues user guidance at safe model boundaries | 64 parameter-schema tokens |
| Codex keeps verifiable objectives alive across turns | Workspace-scoped goals persist independently of sessions, inject a bounded contract, retain 50 checkpoints, and expose their tool only while active | 0 schema tokens outside a goal |
| Grok/Hermes use reviewer and worker agents | `delegate` runs four bounded model workers concurrently; reviewers are read-only and workers return isolated, snapshot-relative Git commits in deterministic order | 112 schema tokens |
| Grok/Codex expose external tools and structured plans | Configured MCP stdio tools are namespaced and bounded; `update_plan` keeps at most 20 validated steps and returns count-only updates | MCP costs zero unless configured; plan schema is measured below |

The map deliberately reuses MJJ's incremental search chunks instead of adding
NetworkX, SQLite, another parser cache, or an always-running service. With the
`syntax` extra installed, tree-sitter remains available for edit validation;
the map itself works in the dependency-free fallback.

## Already covered

- OpenCode's capped tool results, permissions, session branching, compaction,
  format/diagnostic feedback, skills, and provider routing map to MJJ's ledger,
  permission modes, JSONL sessions, Responses compaction, `check`, skills, and
  OpenPaths support.
- Hermes's bounded context injection, skill caching, multimodal preprocessing,
  iteration limits, and compiler fallbacks have corresponding MJJ paths.
- Aider's automatic syntax/lint feedback and multiple edit formats are covered
  by syntax-gated atomic `apply_patch` plus non-blocking compiler checks. MJJ
  keeps one patch format so the permanent prompt stays small.

## High-value follow-ups

1. **Plugin runtime and plan-mode UI.** Portable skills, MCP tools, structured
   plans, and durable goals cover most workflow composition, not in-process
   event hooks, configurable keymaps, or a dependency-aware plan editor.

## Measured cost

`bench/retrieval_bench.py` contains a 120-definition adversarial corpus. On the
audit machine, a 256-character repository-map budget returned three complete
ranked file blocks in 55 estimated tokens versus 1,230 tokens for the raw symbol
listing. Median map construction was 30.197 ms. The two new `list` parameters
cost 43 estimated schema tokens; spill recovery and scoped instructions change
no tool schema.

The follow-up tranche adds 79 tokens for `checkpoint`, 126 for `navigate`, 39
for shell job parameters, and 25 for opt-in formatting. These are estimated
from the exact Responses tool JSON by the same benchmark. Steering changes the
HTTP API and transcript only, so it adds no model tool-schema tokens.

These figures measure harness output and latency, not model task success. They
must be regenerated when the corpus, backend, or machine changes.

The `delegate` schema is 447 minified JSON bytes, or 112 tokens under MJJ's
four-characters-per-token estimator. That is a wire-size measurement, not a
claim about model quality or parallel speedup.

The `update_plan` schema is 493 minified JSON bytes, or 124 tokens under the
same estimator. MCP adds no schema when unconfigured; configured servers pay
only for their capped discovered schemas.
