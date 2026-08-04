from __future__ import annotations

import json
import sys
from pathlib import Path

from mjj.config import MCPServerConfig
from mjj.ledger import Budget, Ledger
from mjj.mcp import _bounded_schema, _tool_name
from mjj.tools import build_registry
from mjj.tools.base import ToolContext


SERVER = r'''
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message.get("method")
    if method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1"},
        }
    elif method == "tools/list":
        tool = {
            "name": "echo-text",
            "description": "Echo compact text",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        }
        if message.get("params", {}).get("cursor"):
            tool["name"] = "echo-second"
            result = {"tools": [tool]}
        else:
            result = {"tools": [tool], "nextCursor": "page-2"}
    elif method == "tools/call":
        text = message["params"]["arguments"]["text"]
        result = {
            "content": [
                {"type": "text", "text": text},
                {"type": "image", "mimeType": "image/png", "data": "AAAA"},
            ],
            "structuredContent": {"length": len(text)},
        }
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
'''


def _server(tmp_path: Path) -> MCPServerConfig:
    path = tmp_path / "fake_mcp.py"
    path.write_text(SERVER, encoding="utf-8")
    return MCPServerConfig(
        name="visual tools",
        command=(sys.executable, str(path)),
        startup_timeout=2,
        tool_timeout=2,
    )


def test_mcp_stdio_discovers_namespaces_calls_and_bounds_binary_content(tmp_path: Path):
    registry = build_registry(only=["mcp"], mcp_servers=(_server(tmp_path),))
    try:
        assert registry.warnings == []
        assert set(registry.tools) == {
            "mcp__visual_tools__echo-text",
            "mcp__visual_tools__echo-second",
        }
        schema = registry.schemas()[0]
        assert schema["parameters"]["required"] == ["text"]

        context = ToolContext(tmp_path, Ledger(Budget(default=200)))
        result = registry.dispatch(
            "mcp__visual_tools__echo-text",
            json.dumps({"text": "hello"}),
            context,
        )

        assert result.ok
        assert "hello" in result.output
        assert "encoded_chars=4" in result.output
        assert '"length":5' in result.output
        assert "AAAA" not in result.output
        assert result.meta["mcp_server"] == "visual tools"

        denied_context = ToolContext(
            tmp_path,
            Ledger(),
            approve=lambda _name, _args: False,
        )
        denied = registry.dispatch(
            "mcp__visual_tools__echo-text",
            json.dumps({"text": "must not run"}),
            denied_context,
        )
        assert not denied.ok
        assert denied.meta["denied"] is True
    finally:
        registry.close()


def test_broken_mcp_server_degrades_to_a_registry_warning(tmp_path: Path):
    config = MCPServerConfig(
        name="missing",
        command=(str(tmp_path / "not-installed"),),
        startup_timeout=0.1,
    )

    registry = build_registry(only=["mcp"], mcp_servers=(config,))

    assert registry.tools == {}
    assert registry.warnings and "could not start" in registry.warnings[0]


def test_mcp_names_and_schemas_fit_model_wire_limits():
    name = _tool_name("server." * 30, "tool/" * 40)
    oversized = {
        "type": "object",
        "properties": {"value": {"type": "string", "description": "x" * 20_000}},
    }

    assert len(name) <= 64
    assert set(name) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    assert _bounded_schema(oversized) == {
        "type": "object",
        "additionalProperties": True,
    }
