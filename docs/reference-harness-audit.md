# Harness reference guide

MJJ borrows mechanisms, not product surfaces. A reference idea belongs here
only when it improves completed coding work per token, keeps output bounded, and
has a useful fallback without a daemon, network service, or optional compiler.

## What to read

These pins, plus dated official product docs where source is unavailable, make
comparisons reviewable. Read the narrow source named for the problem; do not
vendor it.

| harness and pin | useful source | consult it for |
| --- | --- | --- |
| [Aider `5dc9490`](https://github.com/Aider-AI/aider/tree/5dc9490bb35f9729ef2c95d00a19ccd30c26339c) | [`aider/repomap.py`](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/repomap.py), [`aider/linter.py`](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/linter.py) | reference-ranked repository maps and post-edit feedback |
| [OpenCode `c387fe1`](https://github.com/anomalyco/opencode/tree/c387fe190bbd22e9396d264effe242d157f866d2) | [`tool/truncate.ts`](https://github.com/anomalyco/opencode/blob/c387fe190bbd22e9396d264effe242d157f866d2/packages/opencode/src/tool/truncate.ts), [`session/instruction.ts`](https://github.com/anomalyco/opencode/blob/c387fe190bbd22e9396d264effe242d157f866d2/packages/opencode/src/session/instruction.ts), [`snapshot/index.ts`](https://github.com/anomalyco/opencode/blob/c387fe190bbd22e9396d264effe242d157f866d2/packages/opencode/src/snapshot/index.ts) | recoverable truncation, scoped rules, and external edit snapshots |
| [Codex Infinity `4c5ed16`](https://github.com/lee101/codex-infinity/tree/4c5ed168e5becf19bd89df95f8a0bec4e135edb8) | [`codex_thread.rs`](https://github.com/lee101/codex-infinity/blob/4c5ed168e5becf19bd89df95f8a0bec4e135edb8/codex-rs/core/src/codex_thread.rs), [`unified_exec`](https://github.com/lee101/codex-infinity/tree/4c5ed168e5becf19bd89df95f8a0bec4e135edb8/codex-rs/core/src/unified_exec), [`execpolicy`](https://github.com/lee101/codex-infinity/tree/4c5ed168e5becf19bd89df95f8a0bec4e135edb8/codex-rs/execpolicy) | steering, pollable processes, rollout safety, and command policy |
| [Grok Infinity / Grok Build `a293fcd`](https://github.com/lee101/grok-infinity/tree/a293fcd5722d566a237a2e5ce26c827f0be390ef) | [plan mode](https://github.com/lee101/grok-infinity/blob/a293fcd5722d566a237a2e5ce26c827f0be390ef/crates/codegen/xai-grok-pager/docs/user-guide/19-plan-mode.md), [background tasks](https://github.com/lee101/grok-infinity/blob/a293fcd5722d566a237a2e5ce26c827f0be390ef/crates/codegen/xai-grok-pager/docs/user-guide/20-background-tasks.md), [`agent_view/queue.rs`](https://github.com/lee101/grok-infinity/blob/a293fcd5722d566a237a2e5ce26c827f0be390ef/crates/codegen/xai-grok-pager/src/app/agent_view/queue.rs) | plan approval, live prompt queues, workers, and background work |
| [Hermes Agent `91937a6`](https://github.com/NousResearch/hermes-agent/tree/91937a6dc3ffbbe2f3be91a500f0ecf962c4cf53) | [`agent/`](https://github.com/NousResearch/hermes-agent/tree/91937a6dc3ffbbe2f3be91a500f0ecf962c4cf53/agent), [`tools/`](https://github.com/NousResearch/hermes-agent/tree/91937a6dc3ffbbe2f3be91a500f0ecf962c4cf53/tools) | bounded delegation, skills, tool loading, and compiler fallbacks |
| Claude Code docs, audited 2026-08-04 | [interactive mode](https://code.claude.com/docs/en/interactive-mode), [permission modes](https://code.claude.com/docs/en/permission-modes), [checkpointing](https://code.claude.com/docs/en/checkpointing) | per-directory prompt history, plan/permission separation, suggestions, background work, and rewind UX |

Local mirrors may be newer than these pins. Update a pin only after checking
that the cited behavior and MJJ comparison still hold.

## Adoption test

Before implementing a reference pattern, record:

1. the user job or observed failure;
2. the smallest mechanism that addresses it;
3. permanent prompt/schema cost and worst-case result size;
4. behavior when optional dependencies are absent;
5. the test, eval, or benchmark that could reject the change.

Prefer extending an existing tool over adding an always-visible one. A feature
with no task-success evidence, no bounded result policy, or no fallback stays
out even when several harnesses ship it.

For changes to agent behavior, use the layered artifacts and held-out workflow
in the [eval guide](../evals/README.md). A verifier failure is evidence against
the candidate, even when its final prose sounds convincing; a token regression
is published beside any correctness result.

## Patterns already adapted

| reference pattern | MJJ boundary |
| --- | --- |
| Aider's cross-reference-ranked repository map | `mjj/repo_map.py` fits complete, ranked symbol blocks to the caller's budget |
| OpenCode's recoverable truncation | `mjj/ledger.py` bounds every result and stores clipped output mode `0600` under `.mjj/tool-results/` |
| Codex/OpenCode hierarchical rules | `mjj/project_docs.py` loads bounded root-to-CWD rules and discovers nested scope lazily |
| OpenCode snapshots and LSP | `mjj/checkpoints.py` provides hash-safe undo; `mjj/lsp.py` supplies navigation and checkpointed rename |
| Codex/Grok background and steering paths | shell and compiler jobs are pollable; hosted follow-ups enter at safe model boundaries |
| Codex goals and Grok plans/workers | durable goals, bounded plans, and isolated reviewer/worker delegation reuse existing session and Git boundaries |
| Codex/Grok MCP and extension surfaces | MCP and trusted Python tools are opt-in, namespaced, permission-preserving, and bounded |
| Aider's post-edit checks | atomic patches receive syntax validation; compiler and formatter work can continue without blocking the turn loop |

## Codex workflow parity audit

Audited against local Codex commit `f5371bce8729` on 2026-08-05. “Parity” here
means the same coding job has a supported path; it does not mean reproducing
Codex branding, hosted account surfaces, or every full-screen widget.

| Codex workflow | MJJ equivalent | status |
| --- | --- | --- |
| live steering and queued input | Enter steers; Tab queues ordered follow-up turns | equivalent |
| command progress and expandable transcript | compact tool previews; Ctrl-T toggles full bounded output; ledger spills recover clipped raw output | equivalent |
| model, effort, verbosity, and provider selection | `/model`, `/effort`, `/verbosity`, `/provider` plus hotkeys | equivalent |
| permissions and plan approval | `/permissions`; `/plan TASK` enforces read-only until `/plan approve` | equivalent |
| session new/resume/fork/rename/export | `/new`, `/resume`, `/fork`, `/name`, `/export`, `/import`, `/tree` | equivalent |
| manual and automatic compaction | `/compact` plus Responses context management | equivalent with provider fallback |
| review, diff, and undo | `/review`, `/diff`, automatic checkpoints, `/undo` | equivalent |
| background terminals | background `shell`, polling, `/ps`, and `/stop` | equivalent |
| goals, plans, and delegated workers | `/goal`, `update_plan`, and bounded `delegate` | equivalent |
| project rules, mentions, images, skills, plugins, MCP | scoped `AGENTS.md`, `@file`, image commands, lazy skills/plugins/MCP | equivalent |
| side questions (`/side`, `/btw`) | forked sessions via `/fork` or bounded delegated research | workflow equivalent, no ephemeral pane |
| raw/copy-friendly history | inline terminal scrollback, Ctrl-T, `/copy`, `/export` | workflow equivalent |
| IDE selection bridge | `@file`, LSP navigation, and explicit image/file attachment | manual equivalent |
| themes, keymap editor, Vim mode, pets, status-line designer | terminal and prompt-toolkit defaults | intentionally omitted: presentation-only |
| Desktop app link, feedback upload, account limits, memories service | hosted/product-specific services | intentionally omitted from the local harness |
| elevated OS sandbox setup | MJJ permission policy and hosted no-compiler sandbox boundary | intentionally different security boundary |

The audit rejects literal command-count parity: exposing unused commands has
permanent discovery and maintenance cost. A missing workflow is a gap; a
different spelling or a product-only surface is not.

MJJ intentionally keeps one patch format, one transcript protocol, a
dependency-free Python base, and no mandatory index daemon. It does not copy
unbounded project-rule loading, whole provider SDK stacks, or UI/theme systems
that do not improve the coding loop.

## Next comparisons

1. **Plugin lifecycle hooks.** Add events or commands only when a concrete job
   cannot fit the current skills, MCP, or tool-entry-point boundaries without
   permanent prompt cost.
2. **Prompt suggestions and side questions.** Claude generates cache-aware next
   prompts and keeps `/btw` questions out of the main transcript. Evaluate both
   against their extra request cost before adding a model-backed UI feature.

## Reproducible cost

`bench/retrieval_bench.py` contains the adversarial repository-map corpus and
schema accounting. On the recorded audit machine, a 256-character map budget
returned three complete file blocks in 55 estimated tokens versus 1,230 for the
raw symbol list; median construction was 30.197 ms. These measure harness output
and latency, not model task success, and must be regenerated when the corpus,
backend, or machine changes.

The same four-characters-per-token estimator records the optional tool schemas:
`list` map parameters 43 tokens, `checkpoint` 79, `navigate` 137, `delegate`
112, and `update_plan` 124. MCP and plugins cost zero schema tokens when not
configured. Treat these as wire-size measurements, never quality or speedup
claims.
