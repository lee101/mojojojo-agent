"""Small, explicit permission modes shared by the TUI and headless runner."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


PERMISSION_MODES = ("auto", "ask", "read-only")
_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)


@dataclass
class PermissionPolicy:
    mode: str = "auto"
    prompt: Callable[[str], str] | None = None

    def __post_init__(self) -> None:
        self.set(self.mode)

    def set(self, mode: str) -> None:
        if mode not in PERMISSION_MODES:
            raise ValueError("permission mode must be one of: " + ", ".join(PERMISSION_MODES))
        self.mode = mode

    def __call__(self, tool: str, details: dict) -> bool:
        if self.mode == "auto":
            return True
        if self.mode == "read-only" or self.prompt is None:
            return False
        answer = self.prompt(f"Allow {_describe(tool, details)}? [y/N] ")
        return answer.strip().lower() in {"y", "yes"}


def _describe(tool: str, details: dict) -> str:
    if tool == "shell":
        command = str(details.get("command") or "shell command")
        return f"shell command {command[:240]}"
    if tool == "apply_patch":
        files = _PATCH_FILE.findall(str(details.get("input") or ""))
        suffix = ", ".join(files[:8]) or "repository files"
        return f"edits to {suffix}"
    if tool == "checkpoint":
        return f"checkpoint restore {str(details.get('id') or 'latest')[:80]}"
    if tool == "format":
        paths = details.get("paths")
        suffix = ", ".join(map(str, paths[:8])) if isinstance(paths, list) else "files"
        return f"formatting of {suffix}"
    if tool in {"fix", "post_edit"}:
        paths = details.get("paths")
        suffix = ", ".join(map(str, paths[:8])) if isinstance(paths, list) else "files"
        return f"{tool} of {suffix}"
    if tool == "commit":
        message = str(details.get("message") or "commit")
        return f"git commit {message[:120]}"
    if tool == "py":
        return "local Python execution"
    return f"tool {tool}"
