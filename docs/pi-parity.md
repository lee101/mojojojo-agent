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
| Model and reasoning control | `/model`, settings, hotkeys | `/model`, `/provider`, `/effort`, `/verbosity`, left/right and Shift+Up/Down |
| File references | Pi/Grok/Codex `@` completion and attachments | fuzzy `@path`, quoted paths, bounded text, and `@path:START-END` |
| Multimodal prompts | clipboard/files | `/image`, `--image`, or `@image`; quality-85 bounded WebP |
| Direct shell | Pi `!`/`!!`; Codex `!` | `!` includes bounded output in context; `!!` stays local |
| Permissions | Grok/Codex live permission modes | `/permissions` and `--permission-mode`: Auto, Ask, Read-only |
| Repository controls | Codex `/init`, `/status`, `/review`, `/diff`; Grok code review | `/init`, `/status`, `/review [focus]`, `/diff` |
| Session history | `/resume`, `/session`, `/name` | `/history`, `/resume`, `/session`, `/name`, `mjj sessions` |
| Branch and clone | `/tree`, `/fork`, `/clone` | `/tree`, `/tree ITEM`, `/fork`, `/clone`, CLI `--fork` |
| Portable transcripts | `/import`, `/export` | `/import`, `/export`, `mjj import`, `mjj export` |
| Context compaction | automatic and manual | automatic Responses compaction with graceful backend fallback |
| Continued autonomous work | Pi Infinity continuation flags | `--auto-next-steps`, `--auto-next-idea`, `/auto` |
| Skills and project context | skills and context files | scoped `SKILL.md` plus bounded hierarchical `AGENTS.md` |
| Runtime reload | `/reload` | `/reload` for tools and skills |
| Shell and coding tools | read/write/edit/bash/grep/find/ls | read/list/search/apply-patch/shell/Python/native execution |
| Provider reach | direct provider adapters | OpenAI plus OpenPaths/OpenRouter/custom compatible gateways |

## Remaining parity work

These are feature gaps, not presentation preferences:

- **Active-turn steering and follow-up queues.** Pi and Codex accept new input
  while tools are running; Mojo Agent's inline loop is still synchronous.
- **MCP, LSP, and external integrations.** Grok and Codex expose MCP, while
  Grok also supplies language-server diagnostics and navigation.
- **Background jobs and subagents.** Grok can manage long-running tasks and
  reviewer/worker agents. Mojo Agent currently runs one foreground turn loop.
- **Package/plugin runtime.** Pi extensions/packages and Grok/Codex plugins can
  add commands, tools, events, and UI. Mojo Agent supports portable Agent Skills
  but not arbitrary in-process plugins.
- **Plan/goal workflows and configurable keymaps.** Autonomy continuation is
  available, but persistent plans/goals and user keybinding files are not.

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

The implemented surface covers the shared foreground coding job: authenticate,
select a model and reasoning level, attach files or images, inspect and edit a
repository under an explicit permission mode, execute tools, review changes,
continue autonomously, and resume or branch the result. The gaps above define
the next parity milestones.
