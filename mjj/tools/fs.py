"""Bounded filesystem inspection tools."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path

from ..repo_map import render_repo_map
from ..search.index import RepositoryIndex, build_index
from .base import ToolContext, ToolResult

_OUTLINE = re.compile(
    r"^\s*(?:"
    r"#{1,6}\s+\S|"
    r"(?:async\s+)?def\s+\w+|class\s+\w+|"
    r"(?:pub\s+)?(?:fn|struct|enum|trait|impl)\s+\w+|"
    r"(?:export\s+)?(?:async\s+)?function\s+\w+"
    r")"
)
_COLLAPSE_AT = 100
_MAX_RENDERED_LINE = 480


def _result(
    ctx: ToolContext,
    tool: str,
    text: str,
    *,
    ok: bool = True,
    hint: str = "",
    **meta: object,
) -> ToolResult:
    output = ctx.ledger.clip(tool, text, hint)
    return ToolResult(output, ok=ok, meta=dict(meta))


def _integer(args: dict, name: str, default: int | None = None) -> int | None:
    value = args.get(name, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


class ReadTool:
    name = "read"
    description = "Read numbered file lines. Omit a range for a bounded file overview."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path"},
            "start": {"type": "integer", "minimum": 1, "description": "First line"},
            "end": {"type": "integer", "minimum": 1, "description": "Last line, inclusive"},
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        path_arg = args.get("path")
        if not isinstance(path_arg, str) or not path_arg:
            return _result(ctx, self.name, "path must be a non-empty string", ok=False)
        try:
            start = _integer(args, "start")
            end = _integer(args, "end")
        except ValueError as exc:
            return _result(ctx, self.name, str(exc), ok=False)
        if start is not None and start < 1:
            return _result(ctx, self.name, "start must be at least 1", ok=False)
        if end is not None and end < 1:
            return _result(ctx, self.name, "end must be at least 1", ok=False)
        if end is not None and start is None:
            start = 1
        if start is not None and end is not None and end < start:
            return _result(ctx, self.name, "end must not be before start", ok=False)

        path = ctx.resolve(path_arg)
        if not path.exists():
            return _result(ctx, self.name, f"not found: {path_arg}", ok=False)
        if not path.is_file():
            return _result(ctx, self.name, f"not a file: {path_arg}", ok=False)
        try:
            data = path.read_bytes()
        except OSError as exc:
            return _result(ctx, self.name, f"cannot read {path_arg}: {exc}", ok=False)
        if b"\0" in data[:8192]:
            return _result(
                ctx, self.name, f"binary file refused: {path_arg}", ok=False
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return _result(
                ctx, self.name, f"binary or non-UTF-8 file refused: {path_arg}", ok=False
            )

        lines = text.splitlines()
        if start is not None or end is not None:
            first = start or 1
            last = min(end or len(lines), len(lines))
            selected = _numbered(lines, first, last)
            if not selected:
                selected = f"{path_arg}: no lines in range {first}-{end or len(lines)}"
            return _result(
                ctx,
                self.name,
                selected,
                hint=f"read {path_arg} with a narrower start/end range",
                path=str(path),
                start=first,
                end=last,
            )

        numbered = _numbered(lines, 1, len(lines))
        limit = ctx.ledger.budget.for_tool(self.name) * 4
        if len(numbered) <= limit:
            return _result(
                ctx,
                self.name,
                numbered or "<empty file>",
                hint=f"read {path_arg} with start/end",
                path=str(path),
                lines=len(lines),
            )

        head_count = min(30, len(lines))
        outline = [
            f"{number}: {line}"
            for number, line in enumerate(lines, 1)
            if _OUTLINE.match(line)
        ]
        body = [
            f"{path_arg}: {len(lines)} lines (overview)",
            "head:",
            _numbered(lines, 1, head_count),
            "outline:",
            "\n".join(outline) if outline else "(no definitions, classes, or headings)",
        ]
        return _result(
            ctx,
            self.name,
            "\n".join(body),
            hint=f"read {path_arg} with start/end",
            path=str(path),
            lines=len(lines),
            overview=True,
        )


def _numbered(lines: list[str], start: int, end: int) -> str:
    if end < start:
        return ""
    return "\n".join(
        f"{number}: {_bounded_line(lines[number - 1])}"
        for number in range(start, end + 1)
    )


def _bounded_line(line: str) -> str:
    if len(line) <= _MAX_RENDERED_LINE:
        return line
    omitted = len(line) - _MAX_RENDERED_LINE
    return f"{line[:_MAX_RENDERED_LINE]}… [{omitted} chars omitted]"


@dataclass(frozen=True)
class _IgnoreRule:
    base: Path
    pattern: str
    negated: bool
    directory_only: bool
    anchored: bool

    def matches(self, relative: Path, is_dir: bool) -> bool:
        try:
            local = relative.relative_to(self.base).as_posix()
        except ValueError:
            return False
        parts = local.split("/")
        pattern = self.pattern
        if self.anchored or "/" in pattern:
            matched = fnmatch.fnmatchcase(local, pattern)
            if self.directory_only and not matched:
                matched = any(
                    fnmatch.fnmatchcase("/".join(parts[:index]), pattern)
                    for index in range(1, len(parts))
                )
        else:
            matched = any(fnmatch.fnmatchcase(part, pattern) for part in parts)
        if self.directory_only and matched:
            return is_dir or len(parts) > 1
        return matched


def _read_ignore_file(path: Path, base: Path) -> list[_IgnoreRule]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    rules = []
    for raw in lines:
        if not raw or raw.startswith("#"):
            continue
        negated = raw.startswith("!")
        pattern = raw[1:] if negated else raw
        if pattern.startswith(r"\#") or pattern.startswith(r"\!"):
            pattern = pattern[1:]
            negated = False
        pattern = pattern.rstrip()
        directory_only = pattern.endswith("/")
        anchored = pattern.startswith("/")
        pattern = pattern.strip("/")
        if pattern:
            rules.append(
                _IgnoreRule(base, pattern, negated, directory_only, anchored)
            )
    return rules


def _ignored(relative: Path, is_dir: bool, rules: list[_IgnoreRule]) -> bool:
    ignored = relative.parts[:1] in {(".git",), (".mjj",)}
    for rule in rules:
        if rule.matches(relative, is_dir):
            ignored = not rule.negated
    return ignored


class ListTool:
    name = "list"
    description = "List a directory as a sorted, gitignore-aware, depth-limited tree."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path", "default": "."},
            "depth": {
                "type": "integer",
                "minimum": 0,
                "maximum": 20,
                "default": 2,
            },
            "symbols": {
                "type": "boolean",
                "description": "Return a ranked repository symbol map.",
            },
            "query": {
                "type": "string",
                "description": "Optional relevance query for the symbol map.",
            },
        },
        "additionalProperties": False,
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        path_arg = args.get("path", ".")
        if not isinstance(path_arg, str) or not path_arg:
            return _result(ctx, self.name, "path must be a non-empty string", ok=False)
        try:
            depth = _integer(args, "depth", 2)
        except ValueError as exc:
            return _result(ctx, self.name, str(exc), ok=False)
        assert depth is not None
        if depth < 0 or depth > 20:
            return _result(ctx, self.name, "depth must be between 0 and 20", ok=False)
        symbols = args.get("symbols", False)
        if not isinstance(symbols, bool):
            return _result(ctx, self.name, "symbols must be true or false", ok=False)
        query = args.get("query", "")
        if not isinstance(query, str):
            return _result(ctx, self.name, "query must be a string", ok=False)

        root = ctx.resolve(path_arg)
        if not root.exists():
            return _result(ctx, self.name, f"not found: {path_arg}", ok=False)
        if not root.is_dir():
            return _result(ctx, self.name, f"not a directory: {path_arg}", ok=False)

        if symbols:
            workspace = ctx.cwd.resolve()
            try:
                scope = root.relative_to(workspace).as_posix()
            except ValueError:
                return _result(
                    ctx,
                    self.name,
                    "symbol maps must stay inside the workspace",
                    ok=False,
                )
            if scope == ".":
                scope = ""
            cache_key = f"search-index:{workspace}"
            cached = ctx.state.get(cache_key)
            try:
                index = build_index(
                    workspace,
                    existing=cached if isinstance(cached, RepositoryIndex) else None,
                )
                ctx.state[cache_key] = index
                repo_map = render_repo_map(
                    index,
                    scope=scope,
                    query=query,
                    character_budget=ctx.ledger.budget.for_tool(self.name) * 4,
                )
            except (OSError, ValueError) as exc:
                return _result(ctx, self.name, str(exc), ok=False)
            return _result(
                ctx,
                self.name,
                repo_map.output,
                hint="narrow path or add a query",
                path=str(root),
                map=True,
                files=repo_map.files,
                symbols=repo_map.symbols,
                omitted_files=repo_map.omitted_files,
                backend=index.backend_name,
            )

        lines = [f"{path_arg.rstrip('/') or '.'}/"]
        rules = _ancestor_rules(root, ctx.cwd.resolve())
        try:
            _walk(root, root, Path(), depth, 0, rules, lines)
        except OSError as exc:
            return _result(ctx, self.name, f"cannot list {path_arg}: {exc}", ok=False)
        return _result(
            ctx,
            self.name,
            "\n".join(lines),
            hint=f"list {path_arg} with a smaller depth or narrower path",
            path=str(root),
            depth=depth,
        )


def _ancestor_rules(root: Path, workspace: Path) -> list[_IgnoreRule]:
    rules: list[_IgnoreRule] = []
    try:
        relative_root = root.relative_to(workspace)
    except ValueError:
        return _read_ignore_file(root / ".gitignore", Path())
    current = workspace
    rules.extend(_read_ignore_file(current / ".gitignore", Path()))
    relative = Path()
    for part in relative_root.parts:
        current /= part
        relative /= part
        rules.extend(_read_ignore_file(current / ".gitignore", relative))
    return rules


def _walk(
    root: Path,
    directory: Path,
    relative: Path,
    max_depth: int,
    level: int,
    rules: list[_IgnoreRule],
    output: list[str],
) -> None:
    if level >= max_depth:
        return
    local_rules = rules
    if directory != root:
        local_rules = [
            *rules,
            *_read_ignore_file(directory / ".gitignore", relative),
        ]
    entries = []
    for entry in directory.iterdir():
        child_relative = relative / entry.name
        is_dir = entry.is_dir()
        if not _ignored(child_relative, is_dir, local_rules):
            entries.append((entry.name.casefold(), entry.name, entry, child_relative, is_dir))
    entries.sort(key=lambda item: (item[0], item[1]))

    indent = "  " * (level + 1)
    if len(entries) > _COLLAPSE_AT:
        directories = sum(item[4] for item in entries)
        files = len(entries) - directories
        parts = []
        if directories:
            parts.append(f"{directories} director{'y' if directories == 1 else 'ies'}")
        if files:
            parts.append(f"{files} file{'s' if files != 1 else ''}")
        output.append(f"{indent}… {', '.join(parts)}")
        return

    for _, name, entry, child_relative, is_dir in entries:
        output.append(f"{indent}{name}{'/' if is_dir else ''}")
        if is_dir:
            _walk(
                root,
                entry,
                child_relative,
                max_depth,
                level + 1,
                local_rules,
                output,
            )


TOOLS = [ReadTool(), ListTool()]
