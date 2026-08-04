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
provider = "auto"
model = "auto"
effort = "high"
verbosity = "low"
permission_mode = "auto" # auto, ask, or read-only
project_doc_max_bytes = 32768
auto_next_steps = false
auto_next_idea = false
auto_max_turns = 0

[tools]
budget = 1600
disabled = []

[skills]
paths = ["../shared-agent-skills"]
```

Supported environment equivalents are `MJJ_PROVIDER`, `MJJ_MODEL`, `MJJ_EFFORT`,
`MJJ_VERBOSITY`, `MJJ_PERMISSION_MODE`, `MJJ_TOOL_BUDGET`, `MJJ_PROJECT_DOC_MAX_BYTES`,
`MJJ_AUTO_NEXT_STEPS`, `MJJ_AUTO_NEXT_IDEA`, `MJJ_AUTO_MAX_TURNS`,
comma-separated `MJJ_DISABLE_TOOLS`, and path-separator-delimited
`MJJ_SKILL_PATHS`. A zero project-doc budget disables `AGENTS.md` discovery.
An autonomy turn limit of zero means unlimited continuation until interrupted.
Credentials and executor endpoints keep their existing dedicated environment
variables and never appear in `mjj config` output.

Permission mode `auto` approves mutations, `ask` prompts before patches, Python
execution, shell interpretation, and commands outside the read-only allowlist,
and `read-only` denies those operations. Read-only file and Git inspection stay
available in every mode. The CLI flag is `--permission-mode` and the interactive
equivalent is `/permissions`.

Providers are `auto`, `openpaths`, `openrouter`, `openai`, and `custom`.
`custom` reads `MJJ_BASE_URL`, `MJJ_API_KEY`, `MJJ_API_STYLE` (`responses` or
`chat_completions`), and an optional `MJJ_DEFAULT_MODEL`.
