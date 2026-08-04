from __future__ import annotations

import json

from mjj.ledger import Ledger
from mjj.permissions import PermissionPolicy
from mjj.tools.base import Registry, ToolContext
from mjj.tools.patch import ApplyPatchTool
from mjj.tools.py_exec import PyTool
from mjj.tools.shell import ShellTool


def _context(tmp_path, mode: str) -> ToolContext:
    return ToolContext(tmp_path, Ledger(), approve=PermissionPolicy(mode))


def test_read_only_denies_patches_and_unsafe_shell_but_allows_inspection(tmp_path) -> None:
    registry = Registry().add(ApplyPatchTool()).add(ShellTool()).add(PyTool())
    context = _context(tmp_path, "read-only")

    patch = registry.dispatch(
        "apply_patch",
        json.dumps(
            {
                "input": "*** Begin Patch\n*** Add File: made.txt\n+nope\n*** End Patch"
            }
        ),
        context,
    )
    inspection = registry.dispatch("shell", '{"command":["whoami"]}', context)
    mutation = registry.dispatch(
        "shell", '{"command":"touch made.txt","shell":true}', context
    )
    python = registry.dispatch(
        "py", '{"code":"open(\'made.txt\', \'w\').write(\'nope\')"}', context
    )

    assert patch.ok is False and patch.meta["denied"] is True
    assert not (tmp_path / "made.txt").exists()
    assert inspection.ok is True
    assert mutation.ok is False and mutation.meta["denied"] is True
    assert python.ok is False and python.meta["denied"] is True


def test_ask_mode_uses_a_bounded_human_readable_prompt() -> None:
    prompts: list[str] = []
    policy = PermissionPolicy("ask", prompt=lambda message: prompts.append(message) or "yes")

    approved = policy(
        "apply_patch",
        {
            "input": "*** Begin Patch\n*** Update File: src/app.py\n@@\n-secret\n+new\n*** End Patch"
        },
    )

    assert approved is True
    assert prompts == ["Allow edits to src/app.py? [y/N] "]
    assert "secret" not in prompts[0]
