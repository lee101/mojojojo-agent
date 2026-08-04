# Mojo Agent documentation

Mojo Agent (`mjj`) is an open-source coding agent for interactive terminal work,
headless automation, and the hosted mojojojo editor. These pages document the
same code that ships in the [public repository](https://github.com/lee101/mojojojo-agent).

## Start here

- [Getting started](getting-started.md) — install, authenticate, run the first
  task, select providers, and recover a session.
- [Configuration](config.md) — user, project, environment, and CLI precedence.
- [Tools](tools.md) — bounded file, patch, shell, Python, terminal-image, and skill tools.
- [MCP tool servers](mcp.md) — bounded stdio discovery, namespaced calls,
  permissions, configuration, and failure isolation.
- [Skills](skills.md) — discover and load `SKILL.md` workflows without permanent
  prompt overhead.
- [Durable goals](goals.md) — persistent objectives, bounded continuation,
  checkpoints, evidence-backed completion, and lifecycle controls.
- [Reviewer and worker subagents](subagents.md) — parallel read-only review and
  isolated, reviewable implementation commits.

## How it works

- [Architecture](architecture.md) — turn lifecycle, transcript guarantees,
  provider boundary, execution tiers, and hosted isolation.
- [Search](search.md) — exact, lexical, and native vector retrieval.
- [LSP refactors and call hierarchy](lsp-refactors.md) — semantic navigation,
  safe atomic rename, checkpoints, and fallback boundaries.
- [Execution](exec.md) — Mojo acceleration, local isolation, and remote fallback.
- [Visualizers](visualizers.md) — native deterministic WebGL scaffolding,
  image transforms, and token/speed measurements.
- [Reference harness audit](reference-harness-audit.md) — OpenCode, Hermes,
  and Aider ideas adopted, deferred, and measured.
- [Agent feature parity](pi-parity.md) — current Pi Infinity, Grok Infinity,
  and Codex workflow mappings plus the remaining substantive gaps.

## Operate or contribute

- [Hosted server](server.md) — authentication, billing, SSE, workspaces, and API.
- [Visualbench](../visualbench/README.md) — deterministic shader captures and
  screenshot health checks for multimodal workflows.
- [Contributing](../CONTRIBUTING.md) — development setup, tests, and pull requests.
- [Releases](https://github.com/lee101/mojojojo-agent/releases) — checksummed
  Linux, macOS, and Windows binaries.

Secrets never belong in documentation examples or issue reports. `mjj auth`
reports credential state without printing credential values.
