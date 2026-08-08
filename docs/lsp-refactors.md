# LSP refactors, diagnostics, and call hierarchy

The `navigate` tool uses already-installed language servers for semantic
definition, reference, hover, document-symbol, diagnostics, formatting, code
actions, incoming/outgoing calls, and rename. It never downloads a server.

## Read actions

| action | needs position | notes |
| --- | --- | --- |
| `definition` / `references` / `hover` | yes | index fallback when no LSP |
| `symbols` | no | document symbols, or index map fallback |
| `diagnostics` | no | `publishDiagnostics` and/or pull diagnostics |
| `incoming_calls` / `outgoing_calls` | yes | two-step call hierarchy; outgoing needs LSP |
| `code_action` | yes | list actions, or apply one matching `query` |

## Mutating actions

| action | needs position | notes |
| --- | --- | --- |
| `format` | no | `textDocument/formatting` → atomic workspace edit |
| `fix_all` | no | prefers `source.fixAll` code actions |
| `code_action` + `query` | yes | applies the first title/kind match with an edit |
| `rename` | yes | symbol rename via `WorkspaceEdit` |

Mutation contract:

- an installed language server must return a `WorkspaceEdit`; MJJ never uses
  global text replacement as a rename/format fallback;
- only text edits to existing regular files inside the workspace are accepted;
  file create, delete, rename, non-file URI, symlink, and escaping edits fail;
- at most 1,000 edits across 50 files and 8 MiB of output are accepted;
- LSP UTF-16 positions are converted exactly, including astral Unicode;
- overlapping, reversed, malformed, or stale edits fail before writing;
- every changed file passes the existing syntax check;
- Ask and Read-only permission modes apply before mutation;
- the multi-file write is atomic with rollback and an external checkpoint, so
  `/undo` can restore the pre-mutation state unless later edits conflict.

Examples:

```json
{"action":"diagnostics","path":"mjj/agent.py"}
{"action":"format","path":"mjj/agent.py"}
{"action":"fix_all","path":"mjj/agent.py"}
{"action":"code_action","path":"mjj/agent.py","line":151,"column":20,"query":"organize"}
{"action":"incoming_calls","path":"mjj/agent.py","line":151,"column":20}
{"action":"rename","path":"mjj/agent.py","line":151,"column":20,"new_name":"run_turn"}
```

When `tools.post_edit` is `fix` or `full`, successful `apply_patch` also
samples LSP diagnostics for a few touched files so type/lint errors return in
the same tool result.

Hosted sessions retain the existing index-only policy because starting project
language servers may execute repository configuration. Rename, format,
diagnostics, code actions, and outgoing-call requests therefore fail visibly in
hosted mode instead of weakening isolation.
