"""Small stdio LSP client for already-installed language servers."""

from __future__ import annotations

import json
import os
import queue
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


class LspError(RuntimeError):
    pass


MAX_MESSAGE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class LspServer:
    language: str
    name: str
    command: tuple[str, ...]


_SERVERS = {
    "python": (("basedpyright-langserver", "--stdio"), ("pyright-langserver", "--stdio")),
    "typescript": (("typescript-language-server", "--stdio"),),
    "rust": (("rust-analyzer",),),
    "go": (("gopls",),),
    "cpp": (("clangd", "--background-index=false"),),
    "ruby": (("solargraph", "stdio"),),
}
_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".pyi": "python",
    ".js": "typescript",
    ".jsx": "typescriptreact",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".rs": "rust",
    ".go": "go",
    ".c": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
}


def server_for(path: Path) -> LspServer | None:
    language_id = _LANGUAGE_BY_SUFFIX.get(path.suffix.lower())
    if language_id is None:
        return None
    family = "typescript" if language_id.startswith("typescript") else language_id
    override = os.environ.get(f"MJJ_LSP_{family.upper()}")
    if override:
        command = tuple(shlex.split(override))
        if command:
            return LspServer(language_id, Path(command[0]).name, command)
    for candidate in _SERVERS.get(family, ()):
        executable = shutil.which(candidate[0])
        if executable:
            return LspServer(language_id, candidate[0], (executable, *candidate[1:]))
    return None


def request_lsp(
    server: LspServer,
    *,
    root: Path,
    path: Path,
    method: str,
    params: dict,
    timeout: float = 8.0,
):
    client = _Client(server.command, root)
    try:
        client.start(timeout)
        client.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": path.as_uri(),
                    "languageId": server.language,
                    "version": 1,
                    "text": path.read_text(encoding="utf-8", errors="replace"),
                }
            },
        )
        return client.request(method, params, timeout)
    finally:
        client.close()


def request_lsp_call_hierarchy(
    server: LspServer,
    *,
    root: Path,
    path: Path,
    position: dict,
    direction: str,
    timeout: float = 8.0,
):
    """Prepare a call-hierarchy item and request one direction in one session."""
    if direction not in {"incoming", "outgoing"}:
        raise ValueError("call hierarchy direction must be incoming or outgoing")
    client = _Client(server.command, root)
    try:
        client.start(timeout)
        client.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": path.as_uri(),
                    "languageId": server.language,
                    "version": 1,
                    "text": path.read_text(encoding="utf-8", errors="replace"),
                }
            },
        )
        prepared = client.request(
            "textDocument/prepareCallHierarchy",
            {
                "textDocument": {"uri": path.as_uri()},
                "position": position,
            },
            timeout,
        )
        items = prepared if isinstance(prepared, list) else [prepared]
        item = next((value for value in items if isinstance(value, dict)), None)
        if item is None:
            return []
        return client.request(
            f"callHierarchy/{direction}Calls",
            {"item": item},
            timeout,
        )
    finally:
        client.close()


class _Client:
    def __init__(self, command: tuple[str, ...], root: Path) -> None:
        self.command = command
        self.root = root
        self.process: subprocess.Popen | None = None
        self.messages: queue.Queue[dict | None] = queue.Queue()
        self.sequence = 0

    def start(self, timeout: float) -> None:
        try:
            self.process = subprocess.Popen(
                self.command,
                cwd=self.root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise LspError(f"cannot start {self.command[0]}: {exc}") from exc
        assert self.process.stdout is not None
        threading.Thread(
            target=_read_messages,
            args=(self.process.stdout, self.messages),
            name="mjj-lsp-reader",
            daemon=True,
        ).start()
        self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": self.root.as_uri(),
                "capabilities": {"textDocument": {}},
                "clientInfo": {"name": "mjj", "version": "0.3"},
            },
            timeout,
        )
        self.notify("initialized", {})

    def request(self, method: str, params: dict, timeout: float):
        self.sequence += 1
        identifier = self.sequence
        self._send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise queue.Empty
                message = self.messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise LspError(f"{method} timed out after {timeout:g}s") from exc
            if message is None:
                raise LspError(f"language server exited during {method}")
            if "id" in message and isinstance(message.get("method"), str):
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": _client_result(
                            message["method"], message.get("params")
                        ),
                    }
                )
                continue
            if message.get("id") != identifier:
                continue
            if "error" in message:
                detail = message["error"]
                raise LspError(f"{method}: {str(detail)[:300]}")
            return message.get("result")

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _send(self, message: dict) -> None:
        if self.process is None or self.process.stdin is None:
            raise LspError("language server is not running")
        payload = json.dumps(message, separators=(",", ":")).encode()
        try:
            self.process.stdin.write(
                f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload
            )
            self.process.stdin.flush()
        except (OSError, BrokenPipeError) as exc:
            raise LspError("language server pipe closed") from exc

    def close(self) -> None:
        if self.process is None:
            return
        try:
            if self.process.poll() is None:
                self.notify("exit", {})
                self.process.wait(timeout=0.2)
        except (OSError, subprocess.SubprocessError, LspError):
            self.process.terminate()
            try:
                self.process.wait(timeout=0.5)
            except subprocess.SubprocessError:
                self.process.kill()


def _read_messages(stream, messages: queue.Queue[dict | None]) -> None:
    try:
        while True:
            headers: dict[str, str] = {}
            while True:
                line = stream.readline()
                if not line:
                    messages.put(None)
                    return
                if line in {b"\r\n", b"\n"}:
                    break
                name, _, value = line.decode(errors="replace").partition(":")
                headers[name.strip().lower()] = value.strip()
            length = int(headers.get("content-length", "0"))
            if length <= 0:
                continue
            if length > MAX_MESSAGE_BYTES:
                remaining = length
                while remaining:
                    chunk = stream.read(min(remaining, 64 * 1024))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                messages.put(None)
                return
            payload = stream.read(length)
            value = json.loads(payload)
            if isinstance(value, dict):
                messages.put(value)
    except (OSError, ValueError, json.JSONDecodeError):
        messages.put(None)


def _client_result(method: str, params):
    if method == "workspace/configuration":
        items = params.get("items", []) if isinstance(params, dict) else []
        return [None] * len(items)
    if method == "workspace/workspaceFolders":
        return None
    if method == "window/showMessageRequest":
        return None
    return None
