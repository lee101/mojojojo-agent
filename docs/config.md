# Configuration

Configuration resolves in this order, from lowest to highest precedence:

1. built-in defaults;
2. `~/.mjj/config.toml` (or `$MJJ_HOME/config.toml`);
3. the nearest `.mjj/config.toml` between the working directory and Git root;
4. `MJJ_*` environment values;
5. command-line flags.

`mjj config` prints the resolved non-secret values and the files that supplied
them. An explicit `--config PATH` is an additional file layer after the normal
user and project files.

```toml
[agent]
model = "gpt-5.6-sol"
effort = "high"
verbosity = "low"
project_doc_max_bytes = 32768

[tools]
budget = 1600
disabled = []

[skills]
paths = ["../shared-agent-skills"]
```

Supported environment equivalents are `MJJ_MODEL`, `MJJ_EFFORT`,
`MJJ_VERBOSITY`, `MJJ_TOOL_BUDGET`, `MJJ_PROJECT_DOC_MAX_BYTES`,
comma-separated `MJJ_DISABLE_TOOLS`, and path-separator-delimited
`MJJ_SKILL_PATHS`. A zero project-doc budget disables `AGENTS.md` discovery.
Credentials and executor endpoints keep their existing dedicated environment
variables and never appear in `mjj config` output.
