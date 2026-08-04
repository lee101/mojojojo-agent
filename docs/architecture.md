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
   mode appends a synthetic continuation message.

The transcript sent to a provider and the transcript stored on disk share the
same Responses-style item shape. Chat-completions providers are translated in
`mjj/model.py`, outside the loop.

## Context and sessions

Each local session is an append-only JSONL file under `$MJJ_HOME/sessions`.
Resume opens the same file; clone and branch create a new file containing the
selected active context. HTML export is a presentation of that rollout, not a
second source of truth.

Responses server-side compaction replaces the live model window with an opaque
compaction item. The original rollout remains on disk. Backends that do not
support the compaction request are retried without it.

Project context is bounded separately. `AGENTS.md` files are loaded from Git
root to working directory up to the configured byte cap. Skills expose metadata
first and load their full instructions only through the bounded skill tool.

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
| `mjj/agent.py` | Append-only turn loop and tool dispatch |
| `mjj/model.py` | Provider requests, streaming translation, retries, usage |
| `mjj/session.py` | JSONL persistence, resume, branch, import, and export |
| `mjj/tools/` | Bounded coding tools and registry |
| `mjj/search/` | Exact, lexical, and vector retrieval |
| `mjj/server.py` | Hosted workspaces, SSE runs, interrupts, and settlement |
