# Coding-agent feature parity

This comparison was audited in August 2026 against Pi Infinity synced to Pi
0.83.0, the local Grok Infinity user guide and source, and the current official
Codex CLI command reference. Mojo Agent aims for coding-workflow parity while
preserving its Python host, native Mojo acceleration, OpenPaths routing, and
inline terminal UI. It does not claim full parity where the runtime contract is
still missing.

| Coding workflow | Pi / Grok / Codex reference | Mojo Agent |
| --- | --- | --- |
| Interactive default | `pinf` | `mjj` |
| Headless and JSONL | print/JSON modes | `mjj exec`, `--json` |
| In-app authentication | `/login`, `/logout` | `/login`, `/logout`, `/auth` |
| Model and reasoning control | `/model`, settings, hotkeys | value-completed and numbered `/model`; Grok 4.5 and Codex presets; request-time lightweight family prompts; `/provider`, `/reasoning`, `/verbosity`; arrows, F2–F4, and Alt bindings |
| File references | Pi/Grok/Codex `@` completion and attachments | fuzzy `@path`, quoted paths, bounded text, and `@path:START-END` |
| Multimodal prompts | clipboard/files | `/image`, `--image`, or `@image`; quality-85 bounded WebP |
| Images in tool responses | terminal/media attachments | `display_image`, Kitty graphics, bounded ANSI fallback, `/preview` |
| Direct shell | Pi `!`/`!!`; Codex `!` | `!` includes bounded output in context; `!!` stays local |
| Permissions | Grok/Codex live permission modes | `/permissions` and `--permission-mode`: Auto, Ask, Read-only |
| Repository controls | Codex `/init`, `/status`, `/review`, `/diff`; Grok code review | `/init`, `/status`, `/review [focus]`, `/diff` |
| Session history | `/resume`, `/session`, `/name`; Claude per-directory prompt recall | per-directory Up/Down and Ctrl+R prompt history; `/history`, `/resume`, `/session`, `/name`, `mjj sessions` |
| Branch and clone | `/tree`, `/fork`, `/clone` | `/tree`, `/tree ITEM`, `/fork`, `/clone`, CLI `--fork` |
| Portable transcripts | `/import`, `/export` | `/import`, `/export`, `mjj import`, `mjj export` |
| Context compaction | automatic and manual | automatic Responses compaction with graceful backend fallback |
| Continued autonomous work | Pi Infinity continuation flags; Claude separates auto permissions from plans | `/loop steps|ideas|full|forever`, compatible `--auto-next-*` and `/auto`; permissions remain independent |
| Persistent goals | Codex `/goal` and Grok background objectives | `/goal`, `mjj goal`, `--goal`; atomic workspace state, bounded checkpoints, evidence-backed completion |
| Structured plans | Grok Plan mode; Codex plan updates | bounded `update_plan` state and `/plan` inspection |
| Reviewer/worker subagents | Grok delegated workers and review agents | `delegate`: four concurrent bounded children, read-only review, isolated worker commits, deterministic merge |
| External tools | Grok/Codex MCP | configured local stdio MCP, namespaced tools, bounded schemas/results, `/mcp`, safe failure isolation |
| Installed plugin tools | Pi extensions and Grok/Codex plugins | explicit trusted opt-in via Python entry points; namespaced tools, bounded schemas/results, approval preservation, safe failure isolation |
| Skills and project context | skills plus OpenCode/Codex/Claude rule files | scoped `SKILL.md`; bounded hierarchical `AGENTS.override.md`, `AGENTS.md`, `CLAUDE.md`, `CONTEXT.md`; compatible global fallbacks |
| Runtime reload | `/reload` | `/reload` for tools and skills |
| Shell and coding tools | read/write/edit/bash/grep/find/ls | read/list/search/navigate/apply-patch/shell/Python/native execution |
| Semantic navigation/refactors | LSP-backed code intelligence | definition/references/hover/symbols/call hierarchy; approval-gated atomic rename with checkpoint undo |
| Checkpoint and undo | automatic snapshots and undo | external per-patch checkpoints, `/checkpoints`, `/undo` |
| Background execution | compiler and shell jobs | pollable compiler and shell jobs |
| Active steering | follow-up input during a run | bounded hosted follow-up queue at safe model boundaries |
| Provider reach | direct provider adapters | OpenAI plus OpenPaths/OpenRouter/custom compatible gateways |

## Remaining parity work

These are feature gaps, not presentation preferences:

- **Interactive TUI steering.** Hosted runs accept follow-ups while active; the
  inline terminal still waits for the current response before reading input.
- **Plugin commands, events, and UI.** Installed packages can now contribute
  explicitly enabled, bounded tool functions. Pi extensions/packages and
  Grok/Codex plugins can also add commands, event hooks, and UI components.
- **Plan-mode UX and configurable keymaps.** Structured plan state is editable
  by the model and inspectable with `/plan`, but the inline composer does not
  yet provide Grok's dedicated Plan-mode toggle or user keybinding files.

The presentation boundary remains deliberate:

- Pi's full-screen selectors, in-file branch tree, configurable theme engine,
  and TypeScript extension/package runtime remain Pi-specific presentation and
  plugin infrastructure. Mojo Agent uses an inline prompt-toolkit UI, one
  append-only file per branch, portable Agent Skills, and a deliberately small
  tool-only Python plugin API instead.
- Pi's manual lossy `/compact` is not imitated with a fake summary. Mojo Agent
  uses native Responses compaction automatically and retains the original
  append-only rollout for audit and export.
- Pi talks directly to a broader list of provider SDKs. Mojo Agent reaches
  additional models through OpenPaths or OpenRouter so one transcript and tool
  protocol stays consistent.

The implemented surface covers the shared coding job: authenticate,
select a model and reasoning level, attach files or images, inspect and edit a
repository under an explicit permission mode, execute tools, review changes,
continue autonomously, steer hosted work, undo safe patches, and resume or
branch the result. The gaps above define the next parity milestones.
