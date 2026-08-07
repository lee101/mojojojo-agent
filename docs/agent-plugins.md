# Agent Plugins

MJJ loads [Agent Plugins Specification](https://github.com/agentplugins/agent-plugins-spec)
v1.0.0 packages from project and user plugin roots. Each package is a directory
with `plugin.json` plus optional `skills/` and `mcp.json` components.

```text
.agents/plugins/hello-plugin/
├── plugin.json
├── skills/
│   └── greet/
│       └── SKILL.md
└── mcp.json
```

Discovery order:

1. `.agents/plugins/`
2. `.mjj/plugins/`
3. `.codex/plugins/`
4. `$MJJ_HOME/plugins`, `~/.agents/plugins`, `~/.codex/plugins`, `~/.appnz/plugins`

Skills appear in the normal `skill` catalog as `plugin/<name>:<skill>`. Stdio
MCP servers from `mcp.json` start automatically beside configured
`[mcp_servers]` entries; explicit config wins on name collisions. Remote
`streamable-http` / `sse` transports are reported and skipped until MJJ grows a
remote MCP client. `${PLUGIN_ROOT}` and `${PLUGIN_DATA}` expand for stdio
`args`, `env`, and `cwd`. Plugin data lives under `$MJJ_HOME/plugin-data/<name>`.

Broken packages degrade to registry warnings. Python entry-point plugins
(`mojojojo.tools`) remain a separate, explicitly enabled surface — see
[Plugin tools](plugins.md).
