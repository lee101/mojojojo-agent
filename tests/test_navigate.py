from __future__ import annotations

import sys

from mjj.ledger import Ledger
from mjj.lsp import LspServer
from mjj.tools import navigate as navigate_module
from mjj.tools.base import ToolContext
from mjj.tools.navigate import NavigateTool, _apply_text_edits


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


def test_lsp_rename_is_atomic_checked_and_checkpointed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MJJ_CHECKPOINT_ROOT", str(tmp_path / "checkpoints"))
    path = tmp_path / "module.py"
    path.write_text("def old_name():\n    return old_name()\n", encoding="utf-8")
    monkeypatch.setattr(
        navigate_module,
        "server_for",
        lambda _path: LspServer("python", "fixture-lsp", ("fixture",)),
    )
    monkeypatch.setattr(
        navigate_module,
        "request_lsp",
        lambda *_args, **_kwargs: {
            "changes": {
                path.as_uri(): [
                    {
                        "range": {
                            "start": {"line": 0, "character": 4},
                            "end": {"line": 0, "character": 12},
                        },
                        "newText": "new_name",
                    },
                    {
                        "range": {
                            "start": {"line": 1, "character": 11},
                            "end": {"line": 1, "character": 19},
                        },
                        "newText": "new_name",
                    },
                ]
            }
        },
    )
    approvals = []
    context = ToolContext(tmp_path, Ledger(), approve=lambda name, args: approvals.append((name, args)) or True)

    result = NavigateTool().run(
        {
            "action": "rename",
            "path": "module.py",
            "line": 1,
            "column": 6,
            "new_name": "new_name",
        },
        context,
    )

    assert result.ok
    assert path.read_text() == "def new_name():\n    return new_name()\n"
    assert result.meta["checkpoint"]
    assert approvals[0][0] == "rename"
    assert approvals[0][1]["edits"] == 2
    assert context.state["changed-files"] == {"module.py"}


def test_lsp_rename_rejects_escape_before_approval(tmp_path, monkeypatch) -> None:
    path = tmp_path / "module.py"
    outside = tmp_path.parent / "outside.py"
    path.write_text("old_name = 1\n", encoding="utf-8")
    outside.write_text("old_name = 2\n", encoding="utf-8")
    monkeypatch.setattr(
        navigate_module,
        "server_for",
        lambda _path: LspServer("python", "fixture-lsp", ("fixture",)),
    )
    monkeypatch.setattr(
        navigate_module,
        "request_lsp",
        lambda *_args, **_kwargs: {
            "changes": {
                outside.as_uri(): [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 8},
                        },
                        "newText": "new_name",
                    }
                ]
            }
        },
    )
    approvals = []
    context = ToolContext(tmp_path, Ledger(), approve=lambda *_args: approvals.append(True) or True)

    result = NavigateTool().run(
        {
            "action": "rename",
            "path": "module.py",
            "line": 1,
            "column": 2,
            "new_name": "new_name",
        },
        context,
    )

    assert not result.ok and "escapes the workspace" in result.output
    assert approvals == []
    assert outside.read_text() == "old_name = 2\n"


def test_lsp_rename_does_not_overwrite_change_made_during_approval(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "module.py"
    path.write_text("old_name = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        navigate_module,
        "server_for",
        lambda _path: LspServer("python", "fixture-lsp", ("fixture",)),
    )
    monkeypatch.setattr(
        navigate_module,
        "request_lsp",
        lambda *_args, **_kwargs: {
            "changes": {
                path.as_uri(): [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 8},
                        },
                        "newText": "new_name",
                    }
                ]
            }
        },
    )

    def approve(_name, _args):
        path.write_text("newer_user_change = 2\n", encoding="utf-8")
        return True

    result = NavigateTool().run(
        {
            "action": "rename",
            "path": "module.py",
            "line": 1,
            "column": 2,
            "new_name": "new_name",
        },
        ToolContext(tmp_path, Ledger(), approve=approve),
    )

    assert not result.ok and "changed while approval was pending" in result.output
    assert path.read_text() == "newer_user_change = 2\n"


def test_lsp_call_hierarchy_renders_incoming_callers(tmp_path, monkeypatch) -> None:
    path = tmp_path / "module.py"
    path.write_text("def target():\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(
        navigate_module,
        "server_for",
        lambda _path: LspServer("python", "fixture-lsp", ("fixture",)),
    )
    monkeypatch.setattr(
        navigate_module,
        "request_lsp_call_hierarchy",
        lambda *_args, **_kwargs: [
            {
                "from": {
                    "name": "caller",
                    "uri": path.as_uri(),
                    "range": {
                        "start": {"line": 4, "character": 0},
                        "end": {"line": 4, "character": 6},
                    },
                    "selectionRange": {
                        "start": {"line": 4, "character": 4},
                        "end": {"line": 4, "character": 10},
                    },
                },
                "fromRanges": [],
            }
        ],
    )

    result = NavigateTool().run(
        {"action": "incoming_calls", "path": "module.py", "line": 1, "column": 6},
        ToolContext(tmp_path, Ledger()),
    )

    assert result.ok
    assert result.output == "module.py:5:5 caller"
    assert result.meta == {"server": "fixture-lsp", "strategy": "lsp", "results": 1}


def test_rename_requires_lsp_instead_of_text_fallback(tmp_path, monkeypatch) -> None:
    path = tmp_path / "module.py"
    path.write_text("old_name = 1\n", encoding="utf-8")
    monkeypatch.setattr(navigate_module, "server_for", lambda _path: None)

    result = NavigateTool().run(
        {
            "action": "rename",
            "path": "module.py",
            "line": 1,
            "column": 2,
            "new_name": "new_name",
        },
        ToolContext(tmp_path, Ledger()),
    )

    assert not result.ok
    assert "requires an installed language server" in result.output
    assert path.read_text() == "old_name = 1\n"


def test_workspace_edit_positions_use_lsp_utf16_units() -> None:
    source = 'value = "😀"; old_name\n'
    prefix = 'value = "😀"; '
    start = len(prefix.encode("utf-16-le")) // 2

    updated = _apply_text_edits(
        source,
        [
            {
                "range": {
                    "start": {"line": 0, "character": start},
                    "end": {"line": 0, "character": start + len("old_name")},
                },
                "newText": "new_name",
            }
        ],
    )

    assert updated == 'value = "😀"; new_name\n'


def test_navigation_input_position_uses_lsp_utf16_units(tmp_path, monkeypatch) -> None:
    path = tmp_path / "module.py"
    path.write_text("😀target()\n", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(
        navigate_module,
        "server_for",
        lambda _path: LspServer("python", "fixture-lsp", ("fixture",)),
    )

    def request(*_args, **kwargs):
        seen.update(kwargs["params"]["position"])
        return {
            "uri": path.as_uri(),
            "range": {
                "start": {"line": 0, "character": 2},
                "end": {"line": 0, "character": 8},
            },
        }

    monkeypatch.setattr(navigate_module, "request_lsp", request)

    result = NavigateTool().run(
        {"action": "definition", "path": "module.py", "line": 1, "column": 2},
        ToolContext(tmp_path, Ledger()),
    )

    assert result.ok
    assert seen == {"line": 0, "character": 2}
    assert result.output == "module.py:1:2"


def test_navigation_accepts_position_at_trailing_empty_line(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "module.py"
    path.write_text("target()\n", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(
        navigate_module,
        "server_for",
        lambda _path: LspServer("python", "fixture-lsp", ("fixture",)),
    )

    def request(*_args, **kwargs):
        seen.update(kwargs["params"]["position"])
        return None

    monkeypatch.setattr(navigate_module, "request_lsp", request)

    result = NavigateTool().run(
        {"action": "definition", "path": "module.py", "line": 2, "column": 1},
        ToolContext(tmp_path, Ledger()),
    )

    assert seen == {"line": 1, "character": 0}
    assert not result.ok
    assert "no identifier" in result.output
