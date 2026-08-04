"""Language-server navigation with a dependency-free search fallback."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..lsp import LspError, request_lsp, server_for
from ..repo_map import render_repo_map
from ..search.index import RepositoryIndex, build_index
from .base import ToolContext, ToolResult


_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
MAX_LSP_FILE_BYTES = 2 * 1024 * 1024


class NavigateTool:
    name = "navigate"
    description = "Definitions, references, hover, or symbols via installed LSP; search fallback."
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["definition", "references", "hover", "symbols"],
            },
            "path": {"type": "string"},
            "line": {"type": "integer", "minimum": 1},
            "column": {"type": "integer", "minimum": 1},
            "query": {"type": "string"},
        },
        "required": ["action", "path"],
        "additionalProperties": False,
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        action = args.get("action")
        if action not in {"definition", "references", "hover", "symbols"}:
            return self._error(ctx, "action must be definition, references, hover, or symbols")
        path_arg = args.get("path")
        if not isinstance(path_arg, str) or not path_arg:
            return self._error(ctx, "path must be a non-empty string")
        root = ctx.cwd.resolve()
        path = ctx.resolve(path_arg)
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            return self._error(ctx, "navigation path must stay inside the workspace")
        if not path.is_file() or path.is_symlink():
            return self._error(ctx, f"path is not a regular file: {path_arg}")
        query = args.get("query", "")
        if not isinstance(query, str):
            return self._error(ctx, "query must be a string")
        position = None
        if action != "symbols":
            line = args.get("line")
            column = args.get("column")
            if isinstance(line, bool) or not isinstance(line, int) or line < 1:
                return self._error(ctx, "line must be a positive integer")
            if isinstance(column, bool) or not isinstance(column, int) or column < 1:
                return self._error(ctx, "column must be a positive integer")
            position = {"line": line - 1, "character": column - 1}

        server = (
            None
            if ctx.state.get("disable-lsp") or path.stat().st_size > MAX_LSP_FILE_BYTES
            else server_for(path)
        )
        if server is not None:
            try:
                method, params = _request(action, path, position)
                raw = request_lsp(
                    server,
                    root=root,
                    path=path,
                    method=method,
                    params=params,
                )
                output, count = _render_lsp(action, raw, root, query)
                if output:
                    return self._result(
                        ctx,
                        output,
                        server=server.name,
                        strategy="lsp",
                        results=count,
                    )
            except (OSError, ValueError, TypeError, KeyError, LspError):
                pass
        return self._fallback(ctx, action, path, relative, position, query)

    def _fallback(
        self,
        ctx: ToolContext,
        action: str,
        path: Path,
        relative: str,
        position: dict | None,
        query: str,
    ) -> ToolResult:
        root = ctx.cwd.resolve()
        try:
            cache_key = f"search-index:{root}"
            cached = ctx.state.get(cache_key)
            index = build_index(
                root,
                existing=cached if isinstance(cached, RepositoryIndex) else None,
            )
            ctx.state[cache_key] = index
            if action == "symbols":
                repo_map = render_repo_map(
                    index,
                    scope=relative,
                    query=query,
                    character_budget=ctx.ledger.budget.for_tool("navigate") * 4,
                )
                return self._result(
                    ctx,
                    repo_map.output,
                    strategy="index",
                    results=repo_map.symbols,
                )
            assert position is not None
            token = _token_at(path, position["line"], position["character"])
            if not token:
                return self._error(ctx, "no identifier at that position")
            hits = index.search(token, mode="literal", limit=20)
            if action == "definition":
                declarations = [
                    hit
                    for hit in hits
                    if token in hit.chunk.signature and hit.line == hit.chunk.start_line
                ]
                hits = declarations[:8] or hits[:8]
            elif action == "hover":
                hits = hits[:1]
            output = index.format_hits(hits) if hits else "no matches"
            return self._result(
                ctx,
                output,
                strategy="index",
                query=token,
                results=len(hits),
            )
        except (OSError, ValueError) as exc:
            return self._error(ctx, str(exc))

    @staticmethod
    def _result(ctx: ToolContext, text: str, **meta) -> ToolResult:
        return ToolResult(
            ctx.ledger.clip("navigate", text, hint="narrow the symbol or path"),
            meta=meta,
        )

    @staticmethod
    def _error(ctx: ToolContext, text: str) -> ToolResult:
        return ToolResult.error(ctx.ledger.clip("navigate", text))


def _request(action: str, path: Path, position: dict | None) -> tuple[str, dict]:
    document = {"uri": path.as_uri()}
    if action == "symbols":
        return "textDocument/documentSymbol", {"textDocument": document}
    params = {"textDocument": document, "position": position}
    if action == "references":
        params["context"] = {"includeDeclaration": True}
    return f"textDocument/{action}", params


def _render_lsp(action: str, raw, root: Path, query: str) -> tuple[str, int]:
    if raw is None:
        return "", 0
    if action == "hover":
        contents = raw.get("contents") if isinstance(raw, dict) else raw
        text = _hover_text(contents)
        return (text, 1 if text else 0)
    values = raw if isinstance(raw, list) else [raw]
    lines: list[str] = []
    if action == "symbols":
        _flatten_symbols(values, lines, query.lower())
    else:
        for item in values[:20]:
            location = _location(item, root)
            if location:
                lines.append(location)
    return "\n".join(dict.fromkeys(lines)), len(dict.fromkeys(lines))


def _flatten_symbols(values: list, lines: list[str], query: str) -> None:
    for item in values:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not query or query in name.lower():
            location = item.get("location")
            location_range = (
                location.get("range", {}) if isinstance(location, dict) else {}
            )
            range_value = (
                item.get("selectionRange")
                or item.get("range")
                or location_range
            )
            start = range_value.get("start", {}) if isinstance(range_value, dict) else {}
            lines.append(
                f"{int(start.get('line', 0)) + 1}:"
                f"{int(start.get('character', 0)) + 1} {name}"
            )
            if len(lines) >= 20:
                return
        children = item.get("children")
        if isinstance(children, list):
            _flatten_symbols(children, lines, query)
        if len(lines) >= 20:
            return


def _location(item, root: Path) -> str | None:
    if not isinstance(item, dict):
        return None
    uri = item.get("targetUri") or item.get("uri")
    range_value = item.get("targetSelectionRange") or item.get("range") or {}
    if not isinstance(uri, str) or not isinstance(range_value, dict):
        return None
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    path = Path(unquote(parsed.path)).resolve()
    try:
        label = path.relative_to(root).as_posix()
    except ValueError:
        label = str(path)
    start = range_value.get("start", {})
    return f"{label}:{int(start.get('line', 0)) + 1}:{int(start.get('character', 0)) + 1}"


def _hover_text(contents) -> str:
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        return str(contents.get("value") or "")
    if isinstance(contents, list):
        return "\n".join(filter(None, (_hover_text(item) for item in contents)))
    return ""


def _token_at(path: Path, zero_line: int, zero_column: int) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if zero_line < 0 or zero_line >= len(lines):
        return ""
    line = lines[zero_line]
    for match in _IDENTIFIER.finditer(line):
        if match.start() <= zero_column <= match.end():
            return match.group(0)
    return ""


TOOLS = [NavigateTool()]
