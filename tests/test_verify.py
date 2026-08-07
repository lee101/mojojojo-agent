from __future__ import annotations

import json
from pathlib import Path

from mjj.ledger import Ledger
from mjj.tools import build_registry
from mjj.tools.base import ToolContext
from mjj.tools import verify as verify_module
from mjj.tools.verify import VerifyTool, discover_verify


def test_discover_prefers_npm_check(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"check": "node scripts/smoke.mjs", "test": "echo no"}}),
        encoding="utf-8",
    )
    label, argv, shell = discover_verify(tmp_path)
    assert label == "npm run check"
    assert argv[-2:] == ["run", "check"]
    assert shell is False


def test_discover_falls_back_to_pytest(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setattr(verify_module.shutil, "which", lambda name: f"/bin/{name}")
    label, argv, shell = discover_verify(tmp_path)
    assert label == "pytest"
    assert argv[-1] == "-q"
    assert shell is False


def test_verify_tool_runs_override(tmp_path: Path) -> None:
    registry = build_registry(only=["verify"])
    context = ToolContext(tmp_path, Ledger())
    result = registry.dispatch(
        "verify",
        json.dumps({"command": "python -c \"print('ok')\""}),
        context,
    )
    assert result.ok
    assert "verify ✓" in result.output
    assert "ok" in result.output


def test_verify_tool_reports_failure(tmp_path: Path) -> None:
    tool = VerifyTool()
    context = ToolContext(tmp_path, Ledger())
    result = tool.run({"command": "python -c \"raise SystemExit(7)\""}, context)
    assert not result.ok
    assert "exit 7" in result.output
