from __future__ import annotations

import os
import sys
from pathlib import Path

from mjj import syntax
from mjj.ledger import Ledger
from mjj.syntax import validate_source
from mjj.tools import check as check_module
from mjj.tools.base import ToolContext
from mjj.tools.check import CheckTool


def test_exact_builtin_syntax_checks() -> None:
    assert validate_source("ok.py", b"value = 1\n").ok
    assert validate_source("ok.json", b'{"value": 1}').ok
    assert validate_source("ok.toml", b"value = 1\n").ok

    broken = validate_source("broken.py", b"def nope(\n")
    assert broken.checked
    assert not broken.ok
    assert broken.checker == "py_compile"
    assert "line 1" in broken.message


def test_tree_sitter_backend_reports_the_first_error(monkeypatch) -> None:
    class Node:
        type = "ERROR"
        has_error = True
        is_missing = False
        start_point = (2, 4)
        children = []

    class Tree:
        root_node = Node()

    class Parser:
        def parse(self, _data):
            return Tree()

    monkeypatch.setattr(syntax, "_tree_sitter_parser", lambda _language: Parser())
    result = validate_source("broken.js", b"function {\n")

    assert result.checked
    assert not result.ok
    assert result.checker == "tree-sitter-javascript"
    assert result.message == "line 3:5 ERROR"


def test_check_uses_changed_files_and_background_compiler_without_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "module.py"
    path.write_text("answer = 42\n")
    context = ToolContext(tmp_path, Ledger(), state={"changed-files": {"module.py"}})
    environment = dict(os.environ)
    monkeypatch.setattr(
        check_module,
        "_compiler_commands",
        lambda _root, _paths: [
            (
                "py_compile",
                [sys.executable, "-m", "py_compile", str(path)],
                environment,
            )
        ],
    )

    queued = CheckTool().run({"compile": True}, context)
    identifier = queued.meta["compilers"]
    job = context.state["check-jobs"][identifier]
    job.thread.join(timeout=5)
    completed = CheckTool().run({"job": identifier}, context)

    assert queued.ok
    assert "syntax ✓ 1/1" in queued.output
    assert completed.ok
    assert f"compile {identifier} ✓" in completed.output
    assert not (tmp_path / "__pycache__").exists()


def test_check_refuses_workspace_escape(tmp_path: Path) -> None:
    result = CheckTool().run(
        {"paths": ["../outside.py"]}, ToolContext(tmp_path, Ledger())
    )
    assert not result.ok
    assert "inside the workspace" in result.output
