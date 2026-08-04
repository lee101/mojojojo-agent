from __future__ import annotations

import sys

from mjj.ledger import Ledger
from mjj.lsp import LspServer
from mjj.tools import navigate as navigate_module
from mjj.tools.base import ToolContext
from mjj.tools.navigate import NavigateTool


def test_real_stdio_lsp_transport_frames_requests(tmp_path, monkeypatch) -> None:
    server = tmp_path / "fake_lsp.py"
    server.write_text(
        """import json, sys
def receive():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b'\\r\\n', b'\\n'):
            break
        key, value = line.decode().split(':', 1)
        headers[key.lower()] = value.strip()
    return json.loads(sys.stdin.buffer.read(int(headers['content-length'])))
def send(value):
    payload = json.dumps(value, separators=(',', ':')).encode()
    sys.stdout.buffer.write(f'Content-Length: {len(payload)}\\r\\n\\r\\n'.encode() + payload)
    sys.stdout.buffer.flush()
while True:
    message = receive()
    if message is None or message.get('method') == 'exit':
        break
    if 'id' not in message:
        continue
    if message['method'] == 'initialize':
        result = {'capabilities': {}}
    else:
        send({'jsonrpc': '2.0', 'id': message['id'], 'method': 'workspace/configuration', 'params': {'items': [{}, {}]}})
        client_response = receive()
        if client_response.get('result') != [None, None]:
            raise SystemExit(4)
        result = {'uri': message['params']['textDocument']['uri'], 'range': {'start': {'line': 6, 'character': 4}, 'end': {'line': 6, 'character': 8}}}
    send({'jsonrpc': '2.0', 'id': message['id'], 'result': result})
"""
    )
    path = tmp_path / "module.py"
    path.write_text("target()\n")
    monkeypatch.setenv(
        "MJJ_LSP_PYTHON", f"{sys.executable} {server}"
    )

    result = NavigateTool().run(
        {"action": "definition", "path": "module.py", "line": 1, "column": 2},
        ToolContext(tmp_path, Ledger()),
    )

    assert result.ok
    assert result.output == "module.py:7:5"
    assert result.meta["strategy"] == "lsp"


def test_navigation_uses_lsp_locations_when_server_is_installed(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "module.py"
    path.write_text("useful_target()\n")
    monkeypatch.setattr(
        navigate_module,
        "server_for",
        lambda _path: LspServer("python", "fixture-lsp", ("fixture",)),
    )
    monkeypatch.setattr(
        navigate_module,
        "request_lsp",
        lambda *_args, **_kwargs: {
            "uri": path.as_uri(),
            "range": {
                "start": {"line": 4, "character": 2},
                "end": {"line": 4, "character": 8},
            },
        },
    )

    result = NavigateTool().run(
        {"action": "definition", "path": "module.py", "line": 1, "column": 3},
        ToolContext(tmp_path, Ledger()),
    )

    assert result.ok
    assert result.output == "module.py:5:3"
    assert result.meta["strategy"] == "lsp"
    assert result.meta["server"] == "fixture-lsp"


def test_navigation_falls_back_to_index_without_language_server(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / "definition.py").write_text(
        "def useful_target():\n    return 1\n"
    )
    (tmp_path / "caller.py").write_text(
        "from definition import useful_target\nuseful_target()\n"
    )
    monkeypatch.setattr(navigate_module, "server_for", lambda _path: None)
    context = ToolContext(tmp_path, Ledger())

    definition = NavigateTool().run(
        {"action": "definition", "path": "caller.py", "line": 2, "column": 4},
        context,
    )
    references = NavigateTool().run(
        {"action": "references", "path": "caller.py", "line": 2, "column": 4},
        context,
    )

    assert definition.ok and definition.meta["strategy"] == "index"
    assert "definition.py:1" in definition.output
    assert references.meta["results"] >= 2
    assert "caller.py:" in references.output


def test_symbol_navigation_fallback_is_budget_bounded(tmp_path, monkeypatch) -> None:
    for number in range(30):
        (tmp_path / f"module_{number}.py").write_text(
            f"def searchable_symbol_{number}():\n    return {number}\n"
        )
    monkeypatch.setattr(navigate_module, "server_for", lambda _path: None)
    context = ToolContext(tmp_path, Ledger())

    result = NavigateTool().run(
        {"action": "symbols", "path": "module_12.py", "query": "searchable"},
        context,
    )

    assert result.ok
    assert "searchable_symbol_12" in result.output
    assert len(result.output) <= context.ledger.budget.for_tool("navigate") * 4


def test_navigation_rejects_workspace_escape_and_missing_position(tmp_path) -> None:
    outside = tmp_path.parent / "outside.py"
    outside.write_text("value = 1\n")
    context = ToolContext(tmp_path, Ledger())

    escaped = NavigateTool().run(
        {"action": "symbols", "path": str(outside)}, context
    )
    missing = NavigateTool().run(
        {"action": "hover", "path": ".mjj/tool-results/absent"}, context
    )

    assert not escaped.ok and "inside the workspace" in escaped.output
    assert not missing.ok
