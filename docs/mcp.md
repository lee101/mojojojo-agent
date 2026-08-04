# MCP tool servers

MJJ can discover and call tools from explicitly configured local MCP servers
over stdio. Each remote tool is namespaced as `mcp__SERVER__TOOL`, so two
servers cannot silently replace each other or a built-in coding tool.

Configure servers in user or project configuration:

```toml
[mcp_servers.browser]
command = "npx"
args = ["-y", "@example/browser-mcp"]
cwd = "."
env_vars = ["BROWSER_API_KEY"]
startup_timeout = 10
tool_timeout = 120
max_tools = 32
```

`command` may also be an argv array. `cwd` is resolved relative to the config
file. `env_vars` forwards only named variables from MJJ's environment; an
`env = { NAME = "value" }` table is also supported, but environment forwarding
keeps secrets out of files. `mjj config` reports environment key names and
never their values.

MJJ implements the stable MCP `initialize`, `tools/list`, and `tools/call`
surface. Discovery is capped by `max_tools`, individual input schemas larger
than 12 KiB fall back to an open object, and discovered schemas are capped at
32 KiB per server and 64 KiB total. At most 16 servers are configured and up to
eight start concurrently under one deadline each. Descriptions are clipped,
and every result passes through the normal tool ledger. Image and audio content
is represented as metadata rather than injecting encoded bytes into model
context. Use a purpose-built built-in such as `display_image` when the actual
asset belongs in the terminal response chain.

Optional servers fail independently. `/mcp`, `/status`, and `mjj tools` expose
startup warnings while the rest of the harness remains usable. `/reload`
restarts configured servers and refreshes their tool inventory.

MCP calls use the normal permission policy: Auto permits them, Ask confirms
each call, and Read-only denies them. Configuring a stdio server authorizes MJJ
to start that local command during tool discovery, so only configure commands
you trust. The multi-user hosted server does not load local MCP configuration.
