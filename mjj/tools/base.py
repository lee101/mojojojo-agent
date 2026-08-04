"""The tool contract.

Every tool is a ``Tool``: a JSON schema the model sees, and a ``run`` that
takes parsed arguments plus a ``ToolContext`` and returns a ``ToolResult``.
Tools never print, never raise for ordinary failure, and never return output
they have not clipped through the ledger.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ..ledger import Ledger
from ..project_docs import ProjectInstructions, ScopedProjectDocs


_PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
_PATH_KEYS = {"path", "file_path", "filePath", "cwd"}


@dataclass
class ToolContext:
    cwd: Path
    ledger: Ledger
    approve: Callable[[str, dict], bool] | None = None
    state: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cwd = Path(self.cwd).expanduser().resolve()
        self.ledger.bind_workspace(self.cwd)
        self.state.setdefault("scoped-project-docs", ScopedProjectDocs(self.cwd))

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

    def discover_project_docs(self, args: dict) -> ProjectInstructions:
        tracker = self.state.get("scoped-project-docs")
        if not isinstance(tracker, ScopedProjectDocs):
            return ProjectInstructions()
        return tracker.discover(_argument_paths(args, self.cwd))


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
    warnings: list[str] = field(default_factory=list)
    resources: list[Any] = field(default_factory=list, repr=False)

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
            return ToolResult.error(ctx.ledger.clip(name, f"unknown tool {name!r}"))
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except ValueError as exc:
            return ToolResult.error(
                ctx.ledger.clip(name, f"arguments were not valid JSON: {exc}")
            )
        if not isinstance(args, dict):
            return ToolResult.error(
                ctx.ledger.clip(name, "arguments must be a JSON object")
            )
        if getattr(tool, "requires_approval", False) and ctx.approve is not None:
            try:
                approved = ctx.approve(name, args)
            except Exception as exc:
                return ToolResult.error(
                    ctx.ledger.clip(name, f"approval failed: {exc}")
                )
            if not approved:
                return ToolResult.error(
                    ctx.ledger.clip(name, f"tool denied by permission mode: {name}"),
                    denied=True,
                )
        try:
            scoped_docs = ctx.discover_project_docs(args)
        except Exception as exc:
            return ToolResult.error(
                ctx.ledger.clip(
                    name,
                    f"project instruction discovery failed: {type(exc).__name__}: {exc}",
                )
            )
        try:
            result = tool.run(args, ctx)
            if scoped_docs.text:
                result.output = ctx.ledger.attach(name, result.output, scoped_docs.text)
                result.meta["project_docs"] = [
                    str(path) for path in scoped_docs.sources
                ]
            return result
        except Exception as exc:  # a tool crash is a turn event, not a stack trace
            return ToolResult.error(
                ctx.ledger.clip(name, f"{type(exc).__name__}: {exc}")
            )

    def close(self) -> None:
        """Close unique optional backends without making shutdown fragile."""
        resources, self.resources = self.resources, []
        for resource in resources:
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def _argument_paths(args: dict, cwd: Path) -> list[Path]:
    paths: list[Path] = []
    command_cwd = _resolve_candidate(args.get("cwd"), cwd) or cwd
    for key in _PATH_KEYS:
        candidate = _resolve_candidate(args.get(key), cwd)
        if candidate is not None:
            paths.append(candidate)
    raw_paths = args.get("paths")
    if isinstance(raw_paths, list):
        paths.extend(
            candidate
            for value in raw_paths
            if (candidate := _resolve_candidate(value, cwd)) is not None
        )
    patch = args.get("input")
    if isinstance(patch, str):
        paths.extend(
            (cwd / value).resolve()
            for value in _PATCH_PATH.findall(patch)
            if value and not Path(value).is_absolute()
        )
    command = args.get("command")
    if isinstance(command, list):
        tokens = [value for value in command if isinstance(value, str)]
    elif isinstance(command, str):
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
    else:
        tokens = []
    for token in tokens[1:]:
        if token.startswith("-") or token.startswith(("http://", "https://")):
            continue
        value = token.split(":", 1)[0]
        candidate = _resolve_candidate(value, command_cwd)
        if candidate is not None and (
            candidate.exists() or "/" in value or "\\" in value
        ):
            paths.append(candidate)
    return list(dict.fromkeys(paths))


def _resolve_candidate(value, base: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value).expanduser()
    return (
        candidate.resolve()
        if candidate.is_absolute()
        else (base / candidate).resolve()
    )
