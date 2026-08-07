# Configuration

Configuration resolves in this order, from lowest to highest precedence:

1. built-in defaults;
2. `~/.mjj/config.toml` (or `$MJJ_HOME/config.toml`);
3. the nearest `.mjj/config.toml` between the working directory and Git root;
4. `MJJ_*` environment values;
5. command-line flags.

`mjj config` prints the resolved non-secret values and the files that supplied
them. An explicit `--config PATH` is an additional file layer after the normal
user and project files. Interactive `/model`, `/provider`, `/reasoning`, and
`/verbosity` (plus the matching hotkeys) write those defaults back into
`$MJJ_HOME/config.toml` so the next launch keeps them. Choosing a model that
belongs to exactly one concrete provider while `provider = "auto"` also locks
the provider onto that backend.

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

[plugins]
enabled = ["review"]

[mcp_servers.browser]
command = "npx"
args = ["-y", "@example/browser-mcp"]
env_vars = ["BROWSER_API_KEY"]
max_tools = 32
```

Supported environment equivalents are `MJJ_PROVIDER`, `MJJ_MODEL`, `MJJ_EFFORT`,
`MJJ_VERBOSITY`, `MJJ_PERMISSION_MODE`, `MJJ_TOOL_BUDGET`, `MJJ_PROJECT_DOC_MAX_BYTES`,
`MJJ_AUTO_NEXT_STEPS`, `MJJ_AUTO_NEXT_IDEA`, `MJJ_AUTO_MAX_TURNS`,
comma-separated `MJJ_DISABLE_TOOLS`, and path-separator-delimited
`MJJ_SKILL_PATHS`. `MJJ_CACHE_MODE` accepts `auto`, `off`, `implicit`, or
`explicit`; the live interactive control is `/cache`. `MJJ_PLUGINS` is a
comma-separated list of installed plugin
entry-point names. A zero project-doc budget disables `AGENTS.md` discovery.
An autonomy turn limit of zero means unlimited continuation until interrupted.
The concise CLI surface is `--loop steps|ideas|full|forever` plus
`--loop-turns N`; interactive sessions use `/loop` with the same values.
Credentials and executor endpoints keep their existing dedicated environment
variables and never appear in `mjj config` output.

Project-doc discovery includes `AGENTS.override.md`, `AGENTS.md`, `CLAUDE.md`,
and deprecated `CONTEXT.md`, plus one bounded user rule. Set
`MJJ_DISABLE_CLAUDE_CODE_PROMPT=1` to disable `CLAUDE.md` compatibility,
`MJJ_DISABLE_CLAUDE_CODE_SKILLS=1` to disable automatic `.claude/skills`
discovery, or `MJJ_DISABLE_CLAUDE_CODE=1` to disable both. See [project and user
instructions](project-instructions.md) for exact precedence and hosted scope.

MCP servers use explicit local stdio commands. Their `cwd` is relative to the
configuration file; `startup_timeout`, `tool_timeout`, and `max_tools` are
bounded. `env_vars` forwards selected environment variables, while `env` adds
literal values. Resolved config output shows only environment key names. See
[MCP tool servers](mcp.md) for the runtime and permission boundary.

Installed Python plugins are executable code and therefore use a narrower
trust rule than ordinary configuration: `[plugins].enabled` is accepted from
the user config or an explicit `--config` file, but not from a repository's
`.mjj/config.toml`. Command-line `--plugin NAME` and `MJJ_PLUGINS` may also
enable them. See [plugin tools](plugins.md) for the package contract and caps.

Operational tool overrides are also intentionally separate from model
configuration. `MJJ_IMAGE_PROTOCOL` accepts `auto`, `kitty`, `ansi`, or `off`;
automatic mode uses Kitty only in a detected Kitty TTY, uses a small ANSI
preview in other color TTYs, and emits nothing when redirected.
`MJJ_CHECKPOINT_ROOT` relocates the secure external snapshot store.
`MJJ_LSP_PYTHON`, `MJJ_LSP_TYPESCRIPT`, `MJJ_LSP_RUST`, `MJJ_LSP_GO`,
`MJJ_LSP_CPP`, and `MJJ_LSP_RUBY` may name an already-installed language-server
argv; otherwise MJJ discovers the standard executable on `PATH`. These values
never trigger downloads.

Permission mode `auto` approves mutations, `ask` prompts before patches, Python
execution, shell interpretation, and commands outside the read-only allowlist,
and `read-only` denies those operations. Read-only file and Git inspection stay
available in every mode. The CLI flag is `--permission-mode` and the interactive
equivalent is `/permissions`.
Loop and goal modes never change this policy.

Providers are `auto`, `deepseek`, `openpaths`, `openrouter`, `openai`, and `custom`.
DeepSeek uses its OpenAI-compatible API with `DEEPSEEK_API_KEY` (or
`MJJ_DEEPSEEK_API_KEY`) and defaults to `deepseek-v4-flash`; select
`deepseek-v4-pro` for capability-first work.
`custom` reads `MJJ_BASE_URL`, `MJJ_API_KEY`, `MJJ_API_STYLE` (`responses` or
`chat_completions`), and an optional `MJJ_DEFAULT_MODEL`.

Model IDs remain open-ended. Stable coding intents include `auto-code`,
`auto-fast`, `auto-cheap`, `auto-best`, and provider-constrained
`auto-openai*` aliases. The built-in picker also includes `grok-4.5` for
OpenPaths, `x-ai/grok-4.5` for OpenRouter, and current Codex choices for OpenAI.
Concrete Codex and Grok IDs receive one short request-time prompt hint; unknown
and auto-routed IDs receive no extra prompt text. See [models and prompt
caching](models-and-cache.md) for the current route map and cache policy.
