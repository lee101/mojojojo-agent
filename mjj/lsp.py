"""Small stdio LSP client for already-installed language servers."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .platforms import split_command


class LspError(RuntimeError):
    pass


MAX_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_DIAGNOSTICS = 40
SEVERITY_LABELS = {1: "error", 2: "warning", 3: "info", 4: "hint"}


@dataclass(frozen=True)
class LspServer:
    language: str
    name: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class Diagnostic:
    line: int
    column: int
    severity: str
    message: str
    source: str = ""
    code: str = ""

    def render(self, path: str) -> str:
        where = f"{path}:{self.line}:{self.column}"
        code = f" {self.code}" if self.code else ""
        source = f" [{self.source}]" if self.source else ""
        return f"{where} {self.severity}{source}{code}: {self.message}"


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
        command = tuple(split_command(override))
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
        client.open(path, server.language)
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
        client.open(path, server.language)
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


def collect_diagnostics(
    server: LspServer,
    *,
    root: Path,
    path: Path,
    timeout: float = 3.0,
) -> list[Diagnostic]:
    """Open a file and wait briefly for publishDiagnostics / pull diagnostics."""
    client = _Client(server.command, root)
    try:
        client.start(min(timeout, 8.0))
        client.open(path, server.language)
        client.notify(
            "textDocument/didSave",
            {"textDocument": {"uri": path.as_uri()}},
        )
        uri = path.as_uri()
        pulled = client.try_pull_diagnostics(uri, timeout=min(1.0, timeout))
        cached = client.diagnostics.get(uri) or []
        if cached:
            return cached[:MAX_DIAGNOSTICS]
        if pulled:
            return pulled[:MAX_DIAGNOSTICS]
        return client.wait_diagnostics(uri, timeout)[:MAX_DIAGNOSTICS]
    finally:
        client.close()


def format_document(
    server: LspServer,
    *,
    root: Path,
    path: Path,
    timeout: float = 8.0,
) -> list[dict]:
    client = _Client(server.command, root)
    try:
        client.start(timeout)
        client.open(path, server.language)
        result = client.request(
            "textDocument/formatting",
            {
                "textDocument": {"uri": path.as_uri()},
                "options": {"tabSize": 4, "insertSpaces": True},
            },
            timeout,
        )
        if result is None:
            return []
        if not isinstance(result, list):
            raise LspError("formatting result must be an array of text edits")
        return [edit for edit in result if isinstance(edit, dict)]
    finally:
        client.close()


def request_code_actions(
    server: LspServer,
    *,
    root: Path,
    path: Path,
    position: dict | None = None,
    only: tuple[str, ...] = (),
    timeout: float = 8.0,
) -> list[dict]:
    client = _Client(server.command, root)
    try:
        client.start(timeout)
        client.open(path, server.language)
        if position is None:
            range_value = {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 0},
            }
        else:
            range_value = {"start": position, "end": position}
        context: dict = {"diagnostics": []}
        if only:
            context["only"] = list(only)
        result = client.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": path.as_uri()},
                "range": range_value,
                "context": context,
            },
            timeout,
        )
        if result is None:
            return []
        if not isinstance(result, list):
            raise LspError("codeAction result must be an array")
        actions: list[dict] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            # Command-only items without an edit are not auto-applicable.
            if "edit" in item or "arguments" in item or item.get("command"):
                actions.append(item)
            elif "title" in item:
                actions.append(item)
        return actions[:40]
    finally:
        client.close()


class _Client:
    def __init__(self, command: tuple[str, ...], root: Path) -> None:
        self.command = command
        self.root = root
        self.process: subprocess.Popen | None = None
        self.messages: queue.Queue[dict | None] = queue.Queue()
        self.sequence = 0
        self.diagnostics: dict[str, list[Diagnostic]] = {}
        self._diagnostics_event = threading.Event()

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
                "capabilities": {
                    "textDocument": {
                        "synchronization": {"didSave": True},
                        "publishDiagnostics": {},
                        "codeAction": {
                            "codeActionLiteralSupport": {
                                "codeActionKind": {
                                    "valueSet": [
                                        "",
                                        "quickfix",
                                        "refactor",
                                        "source",
                                        "source.organizeImports",
                                        "source.fixAll",
                                    ]
                                }
                            }
                        },
                        "formatting": {},
                        "rename": {},
                        "diagnostic": {},
                    },
                    "workspace": {
                        "workspaceEdit": {
                            "documentChanges": True,
                            "resourceOperations": ["rename", "create", "delete"],
                        },
                        "applyEdit": False,
                    },
                },
                "clientInfo": {"name": "mjj", "version": "0.3"},
            },
            timeout,
        )
        self.notify("initialized", {})

    def open(self, path: Path, language: str) -> None:
        self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": path.as_uri(),
                    "languageId": language,
                    "version": 1,
                    "text": path.read_text(encoding="utf-8", errors="replace"),
                }
            },
        )

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
            if self._ingest_notification(message):
                continue
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

    def wait_diagnostics(self, uri: str, timeout: float) -> list[Diagnostic]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if uri in self.diagnostics:
                return list(self.diagnostics[uri])
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                message = self.messages.get(timeout=min(0.2, remaining))
            except queue.Empty:
                continue
            if message is None:
                break
            if self._ingest_notification(message):
                continue
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
        return list(self.diagnostics.get(uri, []))

    def try_pull_diagnostics(
        self, uri: str, *, timeout: float
    ) -> list[Diagnostic] | None:
        try:
            result = self.request(
                "textDocument/diagnostic",
                {"textDocument": {"uri": uri}},
                timeout,
            )
        except LspError:
            return None
        if not isinstance(result, dict):
            return None
        items = result.get("items")
        if not isinstance(items, list):
            return None
        diagnostics = [
            diagnostic
            for item in items
            if (diagnostic := _parse_diagnostic(item)) is not None
        ]
        if diagnostics or uri not in self.diagnostics:
            self.diagnostics[uri] = diagnostics
        return diagnostics

    def _ingest_notification(self, message: dict) -> bool:
        method = message.get("method")
        if method != "textDocument/publishDiagnostics":
            return False
        params = message.get("params")
        if not isinstance(params, dict):
            return True
        uri = params.get("uri")
        raw = params.get("diagnostics")
        if not isinstance(uri, str) or not isinstance(raw, list):
            return True
        parsed = [
            diagnostic
            for item in raw
            if (diagnostic := _parse_diagnostic(item)) is not None
        ]
        self.diagnostics[uri] = parsed
        self._diagnostics_event.set()
        return True

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


def _parse_diagnostic(item) -> Diagnostic | None:
    if not isinstance(item, dict):
        return None
    range_value = item.get("range")
    if not isinstance(range_value, dict):
        return None
    start = range_value.get("start")
    if not isinstance(start, dict):
        return None
    line = start.get("line")
    character = start.get("character")
    if not isinstance(line, int) or not isinstance(character, int):
        return None
    message = str(item.get("message") or "").strip()
    if not message:
        return None
    severity = SEVERITY_LABELS.get(item.get("severity"), "info")
    code = item.get("code")
    if isinstance(code, dict):
        code = code.get("value")
    return Diagnostic(
        line=line + 1,
        column=character + 1,
        severity=severity,
        message=message[:400],
        source=str(item.get("source") or "")[:40],
        code=str(code or "")[:40],
    )


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
    if method == "window/workDoneProgress/create":
        return None
    if method == "client/registerCapability":
        return None
    return None
