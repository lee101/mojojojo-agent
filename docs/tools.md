# Built-in tools

Every tool implements `mjj.tools.base.Tool` and returns a `ToolResult`. Tool
text, including errors, passes through `ctx.ledger.clip()` exactly once. The
ledger therefore remains the single output-budget policy.

## `read`

`read` accepts `path` and optional one-based, inclusive `start` and `end`
lines. Ranged output is line-numbered. An un-ranged file is returned in full
only when it fits the read budget; larger files return a numbered head and an
outline of definitions, classes, and headings. Binary and non-UTF-8 files are
refused.

```json
{"path": "mjj/agent.py", "start": 40, "end": 90}
```

## `list`

`list` accepts a directory `path` (default `.`) and `depth` (default `2`,
maximum `20`). Entries are sorted by name. Rules from applicable `.gitignore`
files are applied while walking, and `.git` is always omitted. Directories
with more than 100 visible immediate entries are represented by directory and
file counts instead of an entry dump.

```json
{"path": "mjj", "depth": 3}
```

## `shell`

`shell` accepts `command` as either an argv array or a string. Strings are
split into argv by default; no expansion, redirection, pipelines, or command
substitution occurs. Set `"shell": true` explicitly to request system-shell
interpretation. `cwd` is resolved from the agent workspace, and `timeout`
defaults to 120 seconds.

Stdout and stderr are merged in process order. The bounded result ends with an
exit code; timeouts use exit code 124.

A small read-only allowlist runs without approval. It covers inspection
commands such as `cat`, `ls`, `rg`, and read-only Git subcommands. Other
commands call `ctx.approve("shell", details)` when an approval callback is
configured. A missing callback means auto-approve. Shell-interpreted strings
always take the approval path. The interactive `/permissions` command and
`--permission-mode` flag provide Auto, Ask, and Read-only policies.

```json
{"command": ["python", "-m", "pytest", "tests", "-q"], "timeout": 300}
```

## `apply_patch`

`apply_patch` accepts the complete patch as `input`. Paths are relative and
must remain inside the workspace. Supported operations are add, update, and
delete:

```text
*** Begin Patch
*** Add File: notes.txt
+first line
*** Update File: src/example.py
@@ def answer():
-    return 41
+    return 42
*** Delete File: obsolete.txt
*** End Patch
```

Update hunks use space-prefixed context and `-`/`+` lines. `@@` may carry a
class, function, or other line anchor, and multiple anchors can narrow a
location. Context matching retries with trailing and surrounding whitespace
ignored. `*** End of File` constrains a hunk to the file end.

The entire patch is parsed and applied in memory before writes begin. New
contents are staged to temporary files and replaced only after every file and
hunk validates. On success the result contains only per-file `+n/-n` counts,
never file contents.

Patches always pass through the configured approval policy before parsing or
writing. The `py` tool does the same because arbitrary Python can mutate the
workspace. Read-only mode therefore blocks both at the registry boundary.

## `search`

`search` fuses bounded `rg --json` literal candidates, identifier-aware BM25,
and the optional mojo-embed int8 scan. It returns ranked `path:line` evidence,
not file dumps. See [search.md](search.md) for modes, grounding, persistence,
and measured token comparisons.

## `py`

`py` routes safe pure computation through current-interpreter execution and
mojosub's non-blocking tiered JIT. Code requiring isolation uses the local jail
or worker. Missing acceleration and sandbox backends degrade visibly according
to the selected policy. See [exec.md](exec.md).

## `skill`

Calling `skill` with no name returns a compact catalog. Calling it with a short
or qualified name loads that workflow's `SKILL.md`, base directory, and a
bounded bundled-file listing. Project skills are searched before user skills;
ambiguous short names require `scope:name`. Hosted sessions disable user-scope
discovery so the service account's private skills cannot cross into a tenant
workspace. See [skills.md](skills.md).
