# Architecture

Mojo Agent has one append-only agent loop with multiple terminal, headless, and
hosted surfaces. Provider adapters translate wire formats at the edge; tools and
conversation state do not change shape when the model provider changes.

```text
CLI / TUI / hosted SSE
        |
        v
Agent.run  <---- append-only session JSONL
   |                    |
   |                    +---- compaction records retained for audit
   v
ModelClient ---- CredentialResolver ---- OpenAI / OpenPaths / OpenRouter / custom
   |
   v
bounded tool Registry ---- Ledger ---- filesystem / patch / shell / Python / search
                                      |
                                      +---- Mojo acceleration when available
                                      +---- configured local MCP stdio tools
```

## Turn lifecycle

1. The user message and optional bounded image parts are appended to the active
   transcript.
2. `ModelClient` streams reasoning summaries, text, and completed output items.
3. Reasoning and output items are appended verbatim. Encrypted reasoning is not
   rewritten because it must be replayed for continuity and prompt-cache hits.
4. Function calls dispatch through the registry. Tool output passes through one
   ledger before the clipped result is appended and returned to the model.
5. A response without tool calls ends the turn, unless an explicit autonomous
   mode appends a synthetic continuation message. Hosted steering received
   during the response is appended at this safe boundary and starts another
   model pass without concurrent transcript writes.

The transcript sent to a provider and the transcript stored on disk share the
same Responses-style item shape. Chat-completions providers are translated in
`mjj/model.py`, outside the loop.

Immediately before building a provider request, the concrete model ID selects
a tiny prompt profile. Codex and Grok receive one family-specific execution
sentence; neutral and auto-routed models receive none. The hint is inserted
before project instructions, preserving repository-rule precedence, and is
recomputed after a live `/model` switch without rewriting transcript history.

Terminal images follow the same event boundary. `display_image` appends a small
relative-path metadata result; only `mjj/tui.py` resolves and paints it. Native
Kitty graphics and ANSI preview bytes therefore never enter model context,
rollout text, headless stdout, or a hosted shell response.

## Context and sessions

Each local session is an append-only JSONL file under `$MJJ_HOME/sessions`.
Resume opens the same file; clone and branch create a new file containing the
selected active context. HTML export is a presentation of that rollout, not a
second source of truth.

Responses server-side compaction replaces the live model window with an opaque
compaction item. The original rollout remains on disk. Backends that do not
support the compaction request are retried without it.

Project context is bounded separately. Compatible `AGENTS.md`, `CLAUDE.md`, and
fallback files are loaded from Git root to working directory up to the
configured byte cap. Local runs may prepend one bounded user rule; hosted runs
disable that scope. Skills expose metadata first and load their full
instructions only through the bounded skill tool.

Durable goals live outside the transcript in a workspace-keyed, atomic JSON
record. Active goals inject a bounded execution contract and install the goal
tool on demand. Completion or blocking removes that schema again; normal runs
therefore pay no permanent goal-tool context cost.

Structured plans live only in the current tool context and are replaced
atomically through `update_plan`. Configured local MCP clients are owned by the
registry, namespace discovered functions, and close on reload or shutdown.
Their failures remain registry warnings; hosted registries never load them.

## Execution and retrieval

Search fuses exact ripgrep evidence, a compact lexical ranker, and optional
`mojo-embed` vectors. It returns deduplicated, line-anchored evidence rather than
whole files.

Python execution chooses the cheapest safe available tier: in-process for a
small trusted subset, mojosub/native acceleration for supported hot paths, a
local jail for isolation, or the mojojojo execution service. Absence of optional
native components is a performance change, not a startup failure.

## Security boundaries

The local CLI is a developer tool and can modify the repository it was launched
inside. Hosted mode is stricter:

- every account receives a non-identifying workspace root;
- file and search paths are checked against that root;
- hosted shell commands are allowlisted and shell interpretation is disabled;
- hosted background shell jobs and language-server processes are disabled;
- hosted Python fails closed when the local jail is unavailable;
- browser cookies stop at the mojojojo proxy; the backend receives a service
  credential and an already-resolved subject;
- model and tool output queues are bounded.

See [hosted server](server.md) for the HTTP and billing contract, and
[execution](exec.md) for backend selection details.

## Source map

| Module | Responsibility |
| --- | --- |
| `mjj/cli.py` | CLI parsing, headless rendering, and terminal entrypoints |
| `mjj/tui.py` | Interactive composer, hotkeys, slash commands, and rendering |
| `mjj/terminal_images.py` | TTY-safe Kitty and bounded ANSI image presentation |
| `mjj/agent.py` | Append-only turn loop and tool dispatch |
| `mjj/model.py` | Provider requests, streaming translation, retries, usage |
| `mjj/session.py` | JSONL persistence, resume, branch, import, and export |
| `mjj/tools/` | Bounded coding tools and registry |
| `mjj/search/` | Exact, lexical, and vector retrieval |
| `mjj/checkpoints.py` | External bounded snapshots and conflict-safe undo |
| `mjj/lsp.py` | Installed language-server stdio transport |
| `mjj/mcp.py` | Bounded MCP stdio discovery and tool calls |
| `mjj/server.py` | Hosted workspaces, SSE runs, interrupts, and settlement |
