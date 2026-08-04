"""The tool contract.

Every tool is a ``Tool``: a JSON schema the model sees, and a ``run`` that
takes parsed arguments plus a ``ToolContext`` and returns a ``ToolResult``.
Tools never print, never raise for ordinary failure, and never return output
they have not clipped through the ledger.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ..ledger import Ledger


@dataclass
class ToolContext:
    cwd: Path
    ledger: Ledger
    approve: Callable[[str, dict], bool] | None = None
    state: dict = field(default_factory=dict)

    def resolve(self, path: str) -> Path:
        """Resolve a model-supplied path inside the workspace.

        Absolute paths are allowed — the agent legitimately reads
        ``/usr/lib/...`` — but ``..`` escapes are normalised so a relative path
        cannot quietly climb out of the workspace root.
        """
        candidate = Path(path).expanduser()
        if candidate.is_absolute():
            return candidate
        return (self.cwd / candidate).resolve()


@dataclass
class ToolResult:
    output: str
    ok: bool = True
    # Not shown to the model. Carries structured detail for the UI, the
    # rollout file and the tests.
    meta: dict = field(default_factory=dict)

    @classmethod
    def error(cls, message: str, **meta: Any) -> "ToolResult":
        return cls(output=message, ok=False, meta=meta)


class Tool(Protocol):
    name: str
    description: str
    parameters: dict

    def run(self, args: dict, ctx: ToolContext) -> ToolResult: ...


@dataclass
class Registry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def add(self, tool: Tool) -> "Registry":
        self.tools[tool.name] = tool
        return self

    def schemas(self) -> list[dict]:
        """Responses-API function tools. Descriptions are deliberately terse:
        the tool list is resent on every single turn, so a wasted sentence here
        is a wasted sentence times the length of the whole session."""
        return [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "strict": False,
                "parameters": tool.parameters,
            }
            for tool in self.tools.values()
        ]

    def dispatch(self, name: str, arguments: str, ctx: ToolContext) -> ToolResult:
        tool = self.tools.get(name)
        if tool is None:
            return ToolResult.error(f"unknown tool {name!r}")
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except ValueError as exc:
            return ToolResult.error(f"arguments were not valid JSON: {exc}")
        if not isinstance(args, dict):
            return ToolResult.error("arguments must be a JSON object")
        if getattr(tool, "requires_approval", False) and ctx.approve is not None:
            try:
                approved = ctx.approve(name, args)
            except Exception as exc:
                return ToolResult.error(f"approval failed: {exc}")
            if not approved:
                return ToolResult.error(
                    ctx.ledger.clip(name, f"tool denied by permission mode: {name}"),
                    denied=True,
                )
        try:
            return tool.run(args, ctx)
        except Exception as exc:  # a tool crash is a turn event, not a stack trace
            return ToolResult.error(f"{type(exc).__name__}: {exc}")
