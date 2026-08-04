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
| Model and reasoning control | `/model`, settings, hotkeys | value-completed and numbered `/model`; `/provider`, `/reasoning`, `/verbosity`; arrows, F2–F4, and Alt bindings |
| File references | Pi/Grok/Codex `@` completion and attachments | fuzzy `@path`, quoted paths, bounded text, and `@path:START-END` |
| Multimodal prompts | clipboard/files | `/image`, `--image`, or `@image`; quality-85 bounded WebP |
| Images in tool responses | terminal/media attachments | `display_image`, Kitty graphics, bounded ANSI fallback, `/preview` |
| Direct shell | Pi `!`/`!!`; Codex `!` | `!` includes bounded output in context; `!!` stays local |
| Permissions | Grok/Codex live permission modes | `/permissions` and `--permission-mode`: Auto, Ask, Read-only |
| Repository controls | Codex `/init`, `/status`, `/review`, `/diff`; Grok code review | `/init`, `/status`, `/review [focus]`, `/diff` |
| Session history | `/resume`, `/session`, `/name` | `/history`, `/resume`, `/session`, `/name`, `mjj sessions` |
| Branch and clone | `/tree`, `/fork`, `/clone` | `/tree`, `/tree ITEM`, `/fork`, `/clone`, CLI `--fork` |
| Portable transcripts | `/import`, `/export` | `/import`, `/export`, `mjj import`, `mjj export` |
| Context compaction | automatic and manual | automatic Responses compaction with graceful backend fallback |
| Continued autonomous work | Pi Infinity continuation flags | `--auto-next-steps`, `--auto-next-idea`, `/auto` |
| Persistent goals | Codex `/goal` and Grok background objectives | `/goal`, `mjj goal`, `--goal`; atomic workspace state, bounded checkpoints, evidence-backed completion |
| Reviewer/worker subagents | Grok delegated workers and review agents | `delegate`: four concurrent bounded children, read-only review, isolated worker commits, deterministic merge |
| Skills and project context | skills and context files | scoped `SKILL.md` plus bounded hierarchical `AGENTS.md` |
| Runtime reload | `/reload` | `/reload` for tools and skills |
| Shell and coding tools | read/write/edit/bash/grep/find/ls | read/list/search/navigate/apply-patch/shell/Python/native execution |
| Checkpoint and undo | automatic snapshots and undo | external per-patch checkpoints, `/checkpoints`, `/undo` |
| Background execution | compiler and shell jobs | pollable compiler and shell jobs |
| Active steering | follow-up input during a run | bounded hosted follow-up queue at safe model boundaries |
| Provider reach | direct provider adapters | OpenAI plus OpenPaths/OpenRouter/custom compatible gateways |

## Remaining parity work

These are feature gaps, not presentation preferences:

- **MCP and external integrations.** Grok and Codex expose MCP. MJJ now has
  installed-LSP navigation but no authenticated external tool-server runtime.
- **Interactive TUI steering.** Hosted runs accept follow-ups while active; the
  inline terminal still waits for the current response before reading input.
- **Package/plugin runtime.** Pi extensions/packages and Grok/Codex plugins can
  add commands, tools, events, and UI. Mojo Agent supports portable Agent Skills
  but not arbitrary in-process plugins.
- **Plan artifacts and configurable keymaps.** Durable goals now persist their
  objective and verification log, but editable structured plan artifacts and
  user keybinding files are not yet implemented.

The presentation boundary remains deliberate:

- Pi's full-screen selectors, in-file branch tree, configurable theme engine,
  and TypeScript extension/package runtime remain Pi-specific presentation and
  plugin infrastructure. Mojo Agent uses an inline prompt-toolkit UI, one
  append-only file per branch, and portable Agent Skills instead.
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
