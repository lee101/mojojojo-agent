"""Small, bounded MCP stdio client for configured external tools.

The implementation intentionally covers the stable MCP tool surface only:
initialize, tools/list, and tools/call. A broken optional server produces a
registry warning instead of preventing the coding agent from starting.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .config import MCPServerConfig
from .tools.base import ToolContext, ToolResult


PROTOCOL_VERSION = "2025-06-18"
MAX_SCHEMA_BYTES = 12 * 1024
MAX_DESCRIPTION_CHARS = 320
MAX_STDERR_CHARS = 8 * 1024
MAX_MESSAGE_CHARS = 8 * 1024 * 1024
MAX_SERVER_SCHEMA_BYTES = 32 * 1024
MAX_TOTAL_SCHEMA_BYTES = 64 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]+")


class MCPError(RuntimeError):
    pass


@dataclass
class MCPClient:
    config: MCPServerConfig
    process: subprocess.Popen[str] | None = field(init=False, default=None)
    _messages: queue.Queue[dict] = field(init=False, default_factory=queue.Queue)
    _stderr: list[str] = field(init=False, default_factory=list)
    _next_id: int = field(init=False, default=1)
    _lock: threading.Lock = field(init=False, default_factory=threading.Lock)
    inventory_truncated: bool = field(init=False, default=False)

    def start(self) -> list[dict]:
        deadline = time.monotonic() + self.config.startup_timeout

        def startup_request(method: str, params: dict) -> dict:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError(
                    f"startup timed out after {self.config.startup_timeout:g}s"
                )
            return self.request(method, params, timeout=remaining)

        environment = os.environ.copy()
        environment.update(dict(self.config.env))
        try:
            self.process = subprocess.Popen(
                list(self.config.command),
                cwd=self.config.cwd,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise MCPError(f"could not start: {exc}") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        try:
            startup_request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "mjj", "version": "0.3"},
                },
            )
            self.notify("notifications/initialized", {})
            result = startup_request("tools/list", {})
        except Exception:
            self.close()
            raise
        tools: list[dict] = []
        seen_cursors: set[str] = set()
        for _ in range(16):
            page = result.get("tools") if isinstance(result, dict) else None
            if not isinstance(page, list):
                self.close()
                raise MCPError("tools/list returned no tool array")
            for index, tool in enumerate(page):
                if isinstance(tool, dict):
                    tools.append(tool)
                    if len(tools) >= self.config.max_tools:
                        self.inventory_truncated = index + 1 < len(page) or bool(
                            result.get("nextCursor")
                        )
                        return tools
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                return tools
            seen_cursors.add(cursor)
            result = startup_request("tools/list", {"cursor": cursor})
        self.inventory_truncated = bool(result.get("nextCursor"))
        return tools

    def request(self, method: str, params: dict, *, timeout: float) -> dict:
        with self._lock:
            deadline = time.monotonic() + timeout
            request_id = self._next_id
            self._next_id += 1
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    detail = self.stderr_summary()
                    suffix = f"; stderr: {detail}" if detail else ""
                    raise MCPError(f"{method} timed out after {timeout:g}s{suffix}")
                try:
                    message = self._messages.get(timeout=remaining)
                except queue.Empty as exc:
                    detail = self.stderr_summary()
                    suffix = f"; stderr: {detail}" if detail else ""
                    raise MCPError(f"{method} timed out after {timeout:g}s{suffix}") from exc
                if message.get("_transport_error"):
                    raise MCPError(str(message["_transport_error"]))
                if message.get("id") != request_id:
                    if "method" in message and "id" in message:
                        self._send(
                            {
                                "jsonrpc": "2.0",
                                "id": message["id"],
                                "error": {
                                    "code": -32601,
                                    "message": "client method not supported",
                                },
                            }
                        )
                    continue
                error = message.get("error")
                if error:
                    if isinstance(error, dict):
                        error = error.get("message") or json.dumps(error)
                    raise MCPError(f"{method} failed: {error}")
                result = message.get("result", {})
                return result if isinstance(result, dict) else {"value": result}

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout=self.config.tool_timeout,
        )

    def close(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            try:
                process.terminate()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=0.5)
            except (OSError, subprocess.SubprocessError):
                pass
        for stream in (process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

    def stderr_summary(self) -> str:
        return "".join(self._stderr)[-MAX_STDERR_CHARS:].strip()

    def _send(self, message: dict) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise MCPError("server process is not running")
        try:
            process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MCPError(f"server pipe closed: {exc}") from exc

    def _read_stdout(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        while True:
            try:
                line = process.stdout.readline(MAX_MESSAGE_CHARS + 1)
            except (OSError, ValueError):
                return
            if not line:
                if self.process is process:
                    self._messages.put(
                        {"_transport_error": f"MCP server exited with {process.poll()}"}
                    )
                return
            if len(line) > MAX_MESSAGE_CHARS:
                while line and not line.endswith("\n"):
                    line = process.stdout.readline(MAX_MESSAGE_CHARS + 1)
                self._messages.put(
                    {"_transport_error": f"MCP message exceeded {MAX_MESSAGE_CHARS} characters"}
                )
                continue
            try:
                message = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(message, dict):
                self._messages.put(message)

    def _read_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        total = 0
        try:
            for line in process.stderr:
                if total >= MAX_STDERR_CHARS:
                    continue
                line = line[: MAX_STDERR_CHARS - total]
                self._stderr.append(line)
                total += len(line)
        except (OSError, ValueError):
            return


class MCPTool:
    requires_approval = True

    def __init__(self, server: str, spec: dict, client: MCPClient):
        original_name = str(spec.get("name") or "tool")
        self.remote_name = original_name
        self.name = _tool_name(server, original_name)
        description = str(spec.get("description") or f"MCP tool {original_name}")
        self.description = description[:MAX_DESCRIPTION_CHARS]
        self.parameters = _bounded_schema(spec.get("inputSchema"))
        self.client = client

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            result = self.client.call_tool(self.remote_name, args)
        except MCPError as exc:
            return ToolResult.error(ctx.ledger.clip(self.name, str(exc)))
        rendered = _render_content(result)
        return ToolResult(
            output=ctx.ledger.clip(self.name, rendered),
            ok=not bool(result.get("isError")),
            meta={"mcp_server": self.client.config.name, "mcp_tool": self.remote_name},
        )


def discover_mcp_tools(
    configs: tuple[MCPServerConfig, ...] | list[MCPServerConfig],
) -> tuple[list[MCPTool], list[str], list[MCPClient]]:
    tools: list[MCPTool] = []
    warnings: list[str] = []
    clients: list[MCPClient] = []
    configs = list(configs)
    if not configs:
        return tools, warnings, clients

    def start(config: MCPServerConfig):
        client = MCPClient(config)
        try:
            return config, client, client.start(), None
        except Exception as exc:
            client.close()
            return config, None, [], exc

    with ThreadPoolExecutor(max_workers=min(8, len(configs))) as pool:
        started = list(pool.map(start, configs))

    total_schema_bytes = 0
    for config, client, specs, error in started:
        if error is not None or client is None:
            warnings.append(f"MCP {config.name}: {error}")
            continue
        clients.append(client)
        seen = {tool.name for tool in tools}
        schema_bytes = 0
        for spec in specs:
            tool = MCPTool(config.name, spec, client)
            if tool.name in seen:
                digest = hashlib.sha256(
                    f"{config.name}\0{tool.remote_name}".encode("utf-8")
                ).hexdigest()[:10]
                tool.name = tool.name[:53] + "_" + digest
                if tool.name in seen:
                    warnings.append(f"MCP {config.name}: duplicate tool {tool.name}")
                    continue
            cost = len(
                json.dumps(tool.parameters, separators=(",", ":")).encode("utf-8")
            ) + len(tool.description.encode("utf-8"))
            if (
                schema_bytes and schema_bytes + cost > MAX_SERVER_SCHEMA_BYTES
            ) or (
                total_schema_bytes
                and total_schema_bytes + cost > MAX_TOTAL_SCHEMA_BYTES
            ):
                client.inventory_truncated = True
                break
            seen.add(tool.name)
            tools.append(tool)
            schema_bytes += cost
            total_schema_bytes += cost
        if client.inventory_truncated:
            warnings.append(
                f"MCP {config.name}: tool inventory clipped by configured/schema budget"
            )
    return tools, warnings, clients


def _component(value: str) -> str:
    normalized = _SAFE_NAME.sub("_", value).strip("_")
    return (normalized or "tool")[:64]


def _tool_name(server: str, remote: str) -> str:
    name = "mcp__" + _component(server) + "__" + _component(remote)
    if len(name) <= 64:
        return name
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    return name[:53] + "_" + digest


def _bounded_schema(value: Any) -> dict:
    if not isinstance(value, dict) or value.get("type") not in (None, "object"):
        return {"type": "object", "additionalProperties": True}
    try:
        encoded = json.dumps(value, separators=(",", ":"))
    except (TypeError, ValueError):
        return {"type": "object", "additionalProperties": True}
    if len(encoded.encode("utf-8")) > MAX_SCHEMA_BYTES:
        return {"type": "object", "additionalProperties": True}
    return value


def _render_content(result: dict) -> str:
    parts: list[str] = []
    content = result.get("content", [])
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "text":
                parts.append(str(item.get("text") or ""))
            elif kind in ("image", "audio"):
                data = item.get("data")
                size = len(data) if isinstance(data, str) else 0
                parts.append(f"[{kind} {item.get('mimeType', '')} encoded_chars={size}]")
            elif kind == "resource_link":
                parts.append(f"[resource {item.get('name', '')} {item.get('uri', '')}]")
            elif kind == "resource":
                resource = item.get("resource")
                if isinstance(resource, dict) and isinstance(resource.get("text"), str):
                    parts.append(resource["text"])
                elif isinstance(resource, dict):
                    parts.append(f"[resource {resource.get('uri', '')}]")
    structured = result.get("structuredContent")
    if structured is not None:
        try:
            parts.append(json.dumps(structured, ensure_ascii=False, separators=(",", ":")))
        except (TypeError, ValueError):
            parts.append("[unserializable structured content]")
    return "\n".join(part for part in parts if part) or "(no content)"
