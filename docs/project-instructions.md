# Project and user instructions

MJJ automatically loads the common instruction files used by Codex, OpenCode,
and Claude Code. Project rules are bounded and ordered from the Git root to the
working directory. In each directory, the first existing file wins:

1. `AGENTS.override.md`
2. `AGENTS.md`
3. `CLAUDE.md`
4. `CONTEXT.md` (deprecated OpenCode compatibility)

This retains Codex-style hierarchical overrides while allowing an existing
OpenCode or Claude project to work without renaming its rules. An `AGENTS.md`
does not suppress a more specific `CLAUDE.md` in a deeper directory; the deeper
file still applies to its subtree.

Local CLI runs also load the first existing user rule from:

1. `$MJJ_HOME/AGENTS.md` (normally `~/.mjj/AGENTS.md`)
2. `$XDG_CONFIG_HOME/opencode/AGENTS.md` (normally
   `~/.config/opencode/AGENTS.md`)
3. `~/.claude/CLAUDE.md`

Only one user file is loaded. It is presented before project rules so the
repository contract remains more specific. Project files consume the configured
budget first; a user file can use at most 8 KiB of what remains. The total is
controlled by `agent.project_doc_max_bytes`, which defaults to 32 KiB. Set the
value to zero to disable all automatic instruction files.

When a tool first enters a deeper directory, MJJ discovers the applicable file
there and attaches it once to the bounded tool result. This keeps monorepo rules
scoped without permanently expanding the system prompt or invalidating its
cache.

Set `MJJ_DISABLE_CLAUDE_CODE_PROMPT=1` to ignore project and user `CLAUDE.md`
files. Set `MJJ_DISABLE_CLAUDE_CODE_SKILLS=1` to skip automatic
`.claude/skills` discovery. The broader `MJJ_DISABLE_CLAUDE_CODE=1` disables
both compatibility paths. `AGENTS.md`, `AGENTS.override.md`, and deprecated
`CONTEXT.md` remain active. The hosted multi-user service never reads
user-level files from the service account; tenant project files still work.

MJJ intentionally does not fetch remote instruction URLs at startup. Use a
checked-in instruction file, a symlink you control, or an on-demand Agent Skill
instead, so offline startup and token cost stay predictable.
