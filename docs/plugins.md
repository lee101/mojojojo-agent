# Plugin tools

Mojo Agent can load tools exposed by installed Python packages through the
`mojojojo.tools` entry-point group. Plugins are opt-in: listing installed
plugins does not import them, and no plugin code runs until its entry-point
name is enabled from a trusted source.

Enable a plugin for one invocation with a repeatable flag:

```bash
mjj --plugin review exec "inspect this change"
mjj exec --plugin review "inspect this change"
```

For persistent user configuration, add the entry-point name to
`~/.mjj/config.toml`:

```toml
[plugins]
enabled = ["review"]
```

`MJJ_PLUGINS=review,visual` is the environment equivalent. Use `mjj plugins`
or `mjj plugins --json` to inspect installed and enabled entry points without
loading them. A repository's `.mjj/config.toml` cannot enable plugins because
checking out a project must not silently authorize installed Python code.

## Package contract

A package registers a factory, tool, iterable of tools, or object with a
`tools` iterable:

```toml
[project.entry-points."mojojojo.tools"]
review = "review_plugin:build_tools"
```

Each tool follows the normal MJJ tool contract: `name`, `description`, an
object-shaped JSON Schema in `parameters`, and `run(args, context)` returning a
`ToolResult`. Set `requires_approval = true` when a tool can mutate state or
perform another sensitive action. MJJ preserves that approval check.

Plugin tools are exposed to the model as `PLUGIN__TOOL`, such as
`review__comments`. MJJ limits enabled plugins, tools per plugin, descriptions,
and schemas. It clips every result through the normal ledger and spill store,
even when the plugin itself does not. A broken or invalid plugin becomes a
bounded registry warning instead of preventing the agent from starting. If a
plugin object has `close()`, MJJ calls it when the registry closes.

Plugins execute in the MJJ process with the user's Python permissions. Enable
only packages you trust. For separately managed processes or tools that need a
stronger operational boundary, use [MCP tool servers](mcp.md).

The initial plugin API contributes tools only. In-process event hooks, custom
commands, and TUI components are not part of this contract.
