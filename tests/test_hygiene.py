from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from mjj.hygiene import apply_post_edit, fixer_commands, typecheck_commands
from mjj.ledger import Ledger
from mjj.tools.base import ToolContext
from mjj.tools.check import CheckTool
from mjj.tools import check as check_module
from mjj.tools.commit import CommitTool
from mjj.tools.patch import ApplyPatchTool


def test_fixer_discovery_prefers_project_local_ruff(tmp_path: Path) -> None:
    binary = tmp_path / ".venv" / "bin" / "ruff"
    binary.parent.mkdir(parents=True)
    binary.write_text("fixture")
    path = tmp_path / "module.py"
    path.write_text("import os\n")

    commands = fixer_commands(tmp_path, [path])

    assert commands == [("ruff-fix", [str(binary), "check", "--fix", str(path)])]


def test_typecheck_discovery_finds_ruff_then_ty(tmp_path: Path) -> None:
    ruff = tmp_path / ".venv" / "bin" / "ruff"
    ty = tmp_path / ".venv" / "bin" / "ty"
    ruff.parent.mkdir(parents=True)
    ruff.write_text("fixture")
    ty.write_text("fixture")
    path = tmp_path / "module.py"
    path.write_text("value = 1\n")

    commands = typecheck_commands(tmp_path, [path])

    assert commands[0] == ("ruff", [str(ruff), "check", str(path)])
    assert commands[1] == ("ty", [str(ty), "check", str(path)])


def test_check_fix_is_checkpointed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MJJ_CHECKPOINT_ROOT", str(tmp_path / "checkpoints"))
    path = tmp_path / "module.py"
    path.write_text("import os\n")
    context = ToolContext(tmp_path, Ledger())
    monkeypatch.setattr(check_module, "_formatter_commands", lambda _root, _paths: [])
    monkeypatch.setattr(
        check_module,
        "_fixer_commands",
        lambda _root, _paths: [
            (
                "ruff-fix",
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('module.py').write_text('value = 1\\n')",
                ],
            )
        ],
    )

    result = CheckTool().run({"paths": ["module.py"], "fix": True}, context)

    assert result.ok
    assert path.read_text() == "value = 1\n"
    assert result.meta["fixed"] == "ruff-fix"
    assert result.meta["checkpoint"]


def test_check_typecheck_reports_failures(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "module.py"
    path.write_text("value = 1\n")
    context = ToolContext(tmp_path, Ledger())
    monkeypatch.setattr(
        check_module,
        "_typecheck_commands",
        lambda _root, _paths: [
            (
                "ruff",
                [
                    sys.executable,
                    "-c",
                    "import sys; print('module.py:1:1 unused'); raise SystemExit(1)",
                ],
            )
        ],
    )

    result = CheckTool().run({"paths": ["module.py"], "typecheck": True}, context)

    assert not result.ok
    assert "typecheck FAIL" in result.output
    assert "unused" in result.output


def test_post_edit_format_after_patch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MJJ_CHECKPOINT_ROOT", str(tmp_path / "checkpoints"))
    (tmp_path / "module.py").write_text("value=1\n")
    context = ToolContext(tmp_path, Ledger(), state={"post_edit": "format"})
    monkeypatch.setattr(
        "mjj.hygiene.formatter_commands",
        lambda _root, _paths: [
            (
                "fixture",
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('module.py').write_text('value = 1\\n')",
                ],
            )
        ],
    )
    monkeypatch.setattr("mjj.hygiene.fixer_commands", lambda _root, _paths: [])

    result = ApplyPatchTool().run(
        {
            "input": """*** Begin Patch
*** Update File: module.py
@@
-value=1
+value=2
*** End Patch"""
        },
        context,
    )

    assert result.ok
    assert (tmp_path / "module.py").read_text() == "value = 1\n"
    assert "format ✓ fixture" in result.output


def test_post_edit_off_by_default_in_bare_context(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("value = 1\n")
    context = ToolContext(tmp_path, Ledger())
    result = apply_post_edit(context, [tmp_path / "module.py"])
    assert result.text == ""
    assert result.ok


def test_commit_stages_changed_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "mjj@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "mjj"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    path = tmp_path / "notes.txt"
    path.write_text("hello\n")
    subprocess.run(["git", "add", "notes.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    path.write_text("hello\nworld\n")
    context = ToolContext(
        tmp_path, Ledger(), state={"changed-files": {"notes.txt"}}
    )

    result = CommitTool().run({"message": "add world"}, context)

    assert result.ok
    assert "commit ✓" in result.output
    assert "notes.txt" in result.output
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert log.stdout.strip() == "add world"
