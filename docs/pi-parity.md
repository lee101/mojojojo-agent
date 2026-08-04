# Pi Infinity parity

This comparison targets Pi Infinity after its August 2026 sync to upstream Pi
0.83.0. Mojo Agent aims for coding-workflow parity while preserving its Python
host, native Mojo acceleration, OpenPaths routing, and inline terminal UI.

| Coding workflow | Pi Infinity | Mojo Agent |
| --- | --- | --- |
| Interactive default | `pinf` | `mjj` |
| Headless and JSONL | print/JSON modes | `mjj exec`, `--json` |
| In-app authentication | `/login`, `/logout` | `/login`, `/logout`, `/auth` |
| Model and reasoning control | `/model`, settings, hotkeys | `/model`, `/provider`, `/effort`, `/verbosity`, left/right and Shift+Up/Down |
| Multimodal prompts | clipboard/files | `/image`, `mjj exec --image`; quality-85 bounded WebP |
| Session history | `/resume`, `/session`, `/name` | `/history`, `/resume`, `/session`, `/name`, `mjj sessions` |
| Branch and clone | `/tree`, `/fork`, `/clone` | `/tree`, `/tree ITEM`, `/fork`, `/clone`, CLI `--fork` |
| Portable transcripts | `/import`, `/export` | `/import`, `/export`, `mjj import`, `mjj export` |
| Context compaction | automatic and manual | automatic Responses compaction with graceful backend fallback |
| Continued autonomous work | Pi Infinity continuation flags | `--auto-next-steps`, `--auto-next-idea`, `/auto` |
| Skills and project context | skills and context files | scoped `SKILL.md` plus bounded hierarchical `AGENTS.md` |
| Runtime reload | `/reload` | `/reload` for tools and skills |
| Shell and coding tools | read/write/edit/bash/grep/find/ls | read/list/search/apply-patch/shell/Python/native execution |
| Provider reach | direct provider adapters | OpenAI plus OpenPaths/OpenRouter/custom compatible gateways |

The parity boundary is deliberate:

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

These differences do not block the shared coding-agent jobs: authenticate,
select a model and reasoning level, inspect and edit a repository, execute
tools, use images and skills, continue autonomously, and resume or branch the
result.
