# LSP refactors and call hierarchy

The `navigate` tool uses already-installed language servers for semantic
definition, reference, hover, document-symbol, incoming-call, outgoing-call,
and rename operations. It never downloads a server.

Call hierarchy uses the LSP two-step protocol in one server session:
`textDocument/prepareCallHierarchy` selects the symbol, followed by
`callHierarchy/incomingCalls` or `callHierarchy/outgoingCalls`. Results are
bounded to 20 unique `path:line:column` entries. Incoming calls can use the
hybrid index as a conservative reference fallback; outgoing calls require an
LSP because text search cannot infer them safely.

Rename is a workspace mutation and has a stricter contract:

- an installed language server must return a `WorkspaceEdit`; MJJ never uses
  global text replacement as a rename fallback;
- only text edits to existing regular files inside the workspace are accepted;
  file create, delete, rename, non-file URI, symlink, and escaping edits fail;
- at most 1,000 edits across 50 files and 8 MiB of output are accepted;
- LSP UTF-16 positions are converted exactly, including astral Unicode;
- overlapping, reversed, malformed, or stale edits fail before writing;
- every changed file passes the existing syntax check;
- Ask and Read-only permission modes apply before mutation;
- the multi-file write is atomic with rollback and an external checkpoint, so
  `/undo` can restore the pre-rename state unless later edits create a conflict.

Examples:

```json
{"action":"incoming_calls","path":"mjj/agent.py","line":151,"column":20}
{"action":"rename","path":"mjj/agent.py","line":151,"column":20,"new_name":"run_turn"}
```

Hosted sessions retain the existing index-only policy because starting project
language servers may execute repository configuration. Rename and outgoing-call
requests therefore fail visibly in hosted mode instead of weakening isolation.
