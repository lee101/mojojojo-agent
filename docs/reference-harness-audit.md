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
| Hermes discovers nested project hints as tools enter subdirectories | `AGENTS.override.md` / `AGENTS.md` is injected once, on first access to that subtree, with an 8 KiB per-discovery and 32 KiB session ceiling | 0 schema tokens |
| Aider ranks tree-sitter tags by cross-file references and mentioned identifiers | `list` with `symbols=true` ranks indexed declarations by cross-file term references and an optional task query, then pre-fits complete file blocks to the list budget | 43 schema tokens |

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

1. **Transparent checkpoints and undo.** OpenCode and Hermes both keep shadow
   Git snapshots outside the user's repository. MJJ should adopt this only with
   ignored-file, secret, size, retention, and cross-worktree tests; a partial
   undo mechanism is more dangerous than no undo.
2. **LSP navigation.** OpenCode exposes definitions, references, hover, symbols,
   implementations, and call hierarchy. MJJ should first reuse already-installed
   language servers and never download or boot one during an unrelated turn.
3. **Active-turn steering and background workers.** Hermes and OpenCode can
   deliver user steering while tools or subagents run. This requires an evented
   loop and rollout ordering guarantees, not another synchronous tool.
4. **Formatter discovery.** OpenCode detects project-local formatters and runs
   them after edits. MJJ's `check` job is the natural non-blocking host, but
   automatic formatting must remain opt-in because it mutates beyond the patch.

## Measured cost

`bench/retrieval_bench.py` contains a 120-definition adversarial corpus. On the
audit machine, a 256-character repository-map budget returned three complete
ranked file blocks in 55 estimated tokens versus 1,230 tokens for the raw symbol
listing. Median map construction was 33.049 ms. The two new `list` parameters
cost 43 estimated schema tokens; spill recovery and scoped instructions change
no tool schema.

These figures measure harness output and latency, not model task success. They
must be regenerated when the corpus, backend, or machine changes.
