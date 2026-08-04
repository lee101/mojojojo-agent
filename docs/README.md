# Mojo Agent documentation

These pages document the `mjj` CLI, Python package, and hosted agent backend.

## Use MJJ

- [Getting started](getting-started.md) — install, authenticate, run, and resume.
- [Configuration](config.md) — config files, environment variables, and flags.
- [Models and prompt caching](models-and-cache.md) — routes, providers, and
  measured cache behavior.
- [Project instructions](project-instructions.md) and [skills](skills.md) —
  bounded repository guidance loaded only where it applies.
- [Tools](tools.md) — search, read, patch, shell, checks, navigation, and media.
- [Execution](exec.md) — local isolation, Mojo acceleration, and remote fallback.
- [Platforms](platforms.md) — Windows/Linux process and path behavior.
- [MCP](mcp.md) and [plugins](plugins.md) — opt-in external tool boundaries.
- [Goals](goals.md), [subagents](subagents.md), and
  [LSP refactors](lsp-refactors.md) — longer and semantic workflows.

## Understand the harness

- [Architecture](architecture.md) — turn lifecycle and isolation boundaries.
- [Search](search.md) — literal, lexical, and native vector retrieval.
- [Native runtime](native-runtime.md) — optional dependency and Mojo boundaries.
- [Visualizers](visualizers.md) — deterministic WebGL output and measurements.
- [Harness reference guide](reference-harness-audit.md) — pinned Aider,
  OpenCode, Codex, Grok Build, and Hermes source patterns.
- [Feature parity](pi-parity.md) — implemented workflows and remaining gaps.

## Contribute or operate

- [Development guide](../DEV.md) and [contributing](../CONTRIBUTING.md)
- [Hosted server](server.md)
- [Visualbench](../visualbench/README.md)
- [Releases](https://github.com/lee101/mojojojo-agent/releases)

Keep secrets out of examples and issue reports. `mjj auth` reports credential
state without printing credential values.
