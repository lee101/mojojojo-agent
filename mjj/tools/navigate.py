"""Language-server navigation with a dependency-free search fallback."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..checkpoints import CheckpointError, store_for
from ..lsp import LspError, request_lsp, request_lsp_call_hierarchy, server_for
from ..repo_map import render_repo_map
from ..search.index import RepositoryIndex, build_index
from ..syntax import validate_source
from .base import ToolContext, ToolResult
from .patch import _commit, _snapshot


_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
MAX_LSP_FILE_BYTES = 2 * 1024 * 1024
MAX_WORKSPACE_EDIT_FILES = 50
MAX_WORKSPACE_EDITS = 1_000
MAX_WORKSPACE_EDIT_BYTES = 8 * 1024 * 1024
_ACTIONS = {
    "definition",
    "references",
    "hover",
    "symbols",
    "incoming_calls",
    "outgoing_calls",
    "rename",
}


class NavigateTool:
    name = "navigate"
    description = "LSP navigation, call hierarchy, and safe rename."
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": sorted(_ACTIONS),
            },
            "path": {"type": "string"},
            "line": {"type": "integer", "minimum": 1},
            "column": {"type": "integer", "minimum": 1},
            "query": {"type": "string"},
            "new_name": {"type": "string"},
        },
        "required": ["action", "path"],
        "additionalProperties": False,
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        action = args.get("action")
        if action not in _ACTIONS:
            return self._error(ctx, "unknown navigation action")
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
        new_name = args.get("new_name", "")
        if not isinstance(new_name, str):
            return self._error(ctx, "new_name must be a string")
        if action == "rename" and not new_name.strip():
            return self._error(ctx, "rename requires a non-empty new_name")
        if len(new_name) > 256:
            return self._error(ctx, "new_name must not exceed 256 characters")
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
                lsp_position = (
                    _lsp_position(path, position) if position is not None else None
                )
                if action in {"incoming_calls", "outgoing_calls"}:
                    assert lsp_position is not None
                    raw = request_lsp_call_hierarchy(
                        server,
                        root=root,
                        path=path,
                        position=lsp_position,
                        direction=action.removesuffix("_calls"),
                    )
                else:
                    method, params = _request(
                        action,
                        path,
                        lsp_position,
                        new_name=new_name.strip(),
                    )
                    raw = request_lsp(
                        server,
                        root=root,
                        path=path,
                        method=method,
                        params=params,
                    )
                if action == "rename":
                    return self._rename(ctx, raw, new_name.strip(), server.name)
                output, count = _render_lsp(action, raw, root, query, path=path)
                if output:
                    return self._result(
                        ctx,
                        output,
                        server=server.name,
                        strategy="lsp",
                        results=count,
                    )
                if action in {"incoming_calls", "outgoing_calls"}:
                    return self._result(
                        ctx,
                        f"no {action.replace('_', ' ')}",
                        server=server.name,
                        strategy="lsp",
                        results=0,
                    )
            except (OSError, ValueError, TypeError, KeyError, LspError) as exc:
                if action in {"rename", "incoming_calls", "outgoing_calls"}:
                    return self._error(ctx, f"{action}: {exc}")
        if action in {"rename", "outgoing_calls"}:
            return self._error(ctx, f"{action} requires an installed language server")
        return self._fallback(ctx, action, path, relative, position, query)

    def _rename(
        self,
        ctx: ToolContext,
        raw,
        new_name: str,
        server_name: str,
    ) -> ToolResult:
        try:
            plans, originals, edit_count = _plan_workspace_edit(
                raw, ctx.cwd.resolve()
            )
        except (OSError, UnicodeError, ValueError) as exc:
            return self._error(ctx, f"rename rejected: {exc}")
        if not plans:
            return self._error(ctx, "language server returned no rename edits")
        relative = sorted(path.relative_to(ctx.cwd.resolve()).as_posix() for path in plans)
        if ctx.approve is not None:
            try:
                approved = ctx.approve(
                    "rename",
                    {"new_name": new_name, "paths": relative, "edits": edit_count},
                )
            except Exception as exc:
                return self._error(ctx, f"rename approval failed: {exc}")
            if not approved:
                return ToolResult.error(
                    ctx.ledger.clip("navigate", "rename denied"), denied=True
                )
        snapshots = {path: _snapshot(path) for path in plans}
        if any(
            snapshot.content != originals[path]
            for path, snapshot in snapshots.items()
        ):
            return self._error(ctx, "rename targets changed while approval was pending")
        checkpoint = None
        checkpoint_error = ""
        store = None
        pending = None
        try:
            store = store_for(ctx.cwd, ctx.state)
            pending = store.begin(plans)
        except (OSError, CheckpointError) as exc:
            checkpoint_error = str(exc)
        try:
            _commit(plans, snapshots)
        except OSError as exc:
            if pending is not None and store is not None:
                store.cancel(pending)
            return self._error(ctx, f"rename failed: {exc}")
        if pending is not None and store is not None:
            try:
                checkpoint = store.finish(pending, expected=plans).identifier
            except (OSError, CheckpointError) as exc:
                store.cancel(pending)
                checkpoint_error = str(exc)
        changed = ctx.state.setdefault("changed-files", set())
        if isinstance(changed, set):
            changed.update(relative)
        lines = [
            f"rename ✓ {edit_count} edit{'' if edit_count == 1 else 's'} "
            f"across {len(plans)} file{'' if len(plans) == 1 else 's'}",
            *relative,
        ]
        if checkpoint:
            lines[0] += f" · checkpoint {checkpoint}"
        elif checkpoint_error:
            lines.append(f"checkpoint unavailable: {checkpoint_error}")
        return self._result(
            ctx,
            "\n".join(lines),
            strategy="lsp",
            server=server_name,
            edits=edit_count,
            files=len(plans),
            checkpoint=checkpoint,
            checkpoint_error=checkpoint_error,
        )

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


def _request(
    action: str,
    path: Path,
    position: dict | None,
    *,
    new_name: str = "",
) -> tuple[str, dict]:
    document = {"uri": path.as_uri()}
    if action == "symbols":
        return "textDocument/documentSymbol", {"textDocument": document}
    params = {"textDocument": document, "position": position}
    if action == "references":
        params["context"] = {"includeDeclaration": True}
    if action == "rename":
        params["newName"] = new_name
    return f"textDocument/{action}", params


def _render_lsp(
    action: str,
    raw,
    root: Path,
    query: str,
    *,
    path: Path | None = None,
) -> tuple[str, int]:
    if raw is None:
        return "", 0
    if action == "hover":
        contents = raw.get("contents") if isinstance(raw, dict) else raw
        text = _hover_text(contents)
        return (text, 1 if text else 0)
    if action in {"incoming_calls", "outgoing_calls"}:
        return _render_calls(action, raw, root)
    values = raw if isinstance(raw, list) else [raw]
    lines: list[str] = []
    if action == "symbols":
        _flatten_symbols(values, lines, query.lower(), path)
    else:
        for item in values[:20]:
            location = _location(item, root)
            if location:
                lines.append(location)
    return "\n".join(dict.fromkeys(lines)), len(dict.fromkeys(lines))


def _flatten_symbols(
    values: list,
    lines: list[str],
    query: str,
    path: Path | None,
) -> None:
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
            line = int(start.get("line", 0))
            character = int(start.get("character", 0))
            column = _display_column(path, line, character) if path is not None else character + 1
            lines.append(
                f"{line + 1}:{column} {name}"
            )
            if len(lines) >= 20:
                return
        children = item.get("children")
        if isinstance(children, list):
            _flatten_symbols(children, lines, query, path)
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
        inside = True
    except ValueError:
        label = str(path)
        inside = False
    start = range_value.get("start", {})
    line = int(start.get("line", 0))
    character = int(start.get("character", 0))
    column = _display_column(path, line, character) if inside else character + 1
    return f"{label}:{line + 1}:{column}"


def _display_column(path: Path, line: int, character: int) -> int:
    """Translate an LSP UTF-16 column to a one-based code-point column."""
    try:
        if path.stat().st_size > MAX_LSP_FILE_BYTES:
            return character + 1
        source = path.read_text(encoding="utf-8", errors="replace")
        lines = source.splitlines()
        if source.endswith(("\n", "\r")):
            lines.append("")
        text = lines[line]
    except (OSError, IndexError):
        return character + 1
    units = 0
    for index, value in enumerate(text):
        if units == character:
            return index + 1
        units += len(value.encode("utf-16-le")) // 2
        if units > character:
            return character + 1
    return len(text) + 1 if units == character else character + 1


def _hover_text(contents) -> str:
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        return str(contents.get("value") or "")
    if isinstance(contents, list):
        return "\n".join(filter(None, (_hover_text(item) for item in contents)))
    return ""


def _render_calls(action: str, raw, root: Path) -> tuple[str, int]:
    values = raw if isinstance(raw, list) else []
    lines = []
    key = "from" if action == "incoming_calls" else "to"
    for value in values[:20]:
        item = value.get(key) if isinstance(value, dict) else None
        if not isinstance(item, dict):
            continue
        location = _location(
            {
                "uri": item.get("uri"),
                "range": item.get("selectionRange") or item.get("range"),
            },
            root,
        )
        if location:
            name = str(item.get("name") or "")
            lines.append(f"{location} {name}".rstrip())
    unique = list(dict.fromkeys(lines))
    return "\n".join(unique), len(unique)


def _plan_workspace_edit(
    raw,
    root: Path,
) -> tuple[dict[Path, bytes | None], dict[Path, bytes], int]:
    if raw is None:
        return {}, {}, 0
    if not isinstance(raw, dict):
        raise ValueError("workspace edit must be an object")
    grouped: dict[Path, list[dict]] = {}
    changes = raw.get("changes", {})
    if changes is not None:
        if not isinstance(changes, dict):
            raise ValueError("workspace edit changes must be an object")
        for uri, edits in changes.items():
            _collect_text_edits(grouped, root, uri, edits)
    document_changes = raw.get("documentChanges", [])
    if document_changes is not None:
        if not isinstance(document_changes, list):
            raise ValueError("workspace edit documentChanges must be an array")
        for change in document_changes:
            if not isinstance(change, dict) or not isinstance(
                change.get("textDocument"), dict
            ):
                raise ValueError("rename file operations are not supported")
            _collect_text_edits(
                grouped,
                root,
                change["textDocument"].get("uri"),
                change.get("edits"),
            )
    if len(grouped) > MAX_WORKSPACE_EDIT_FILES:
        raise ValueError(f"rename exceeds {MAX_WORKSPACE_EDIT_FILES} files")
    edit_count = sum(len(edits) for edits in grouped.values())
    if edit_count > MAX_WORKSPACE_EDITS:
        raise ValueError(f"rename exceeds {MAX_WORKSPACE_EDITS} edits")
    plans: dict[Path, bytes | None] = {}
    originals: dict[Path, bytes] = {}
    total = 0
    for path, edits in grouped.items():
        original = path.read_bytes()
        source = original.decode("utf-8")
        updated = _apply_text_edits(source, edits)
        content = updated.encode("utf-8")
        total += len(content)
        if total > MAX_WORKSPACE_EDIT_BYTES:
            raise ValueError("rename output exceeds 8 MiB")
        relative = path.relative_to(root).as_posix()
        check = validate_source(relative, content)
        if check.checked and not check.ok:
            raise ValueError(
                f"syntax check failed: {relative} [{check.checker}] {check.message}"
            )
        plans[path] = content
        originals[path] = original
    return plans, originals, edit_count


def _collect_text_edits(
    grouped: dict[Path, list[dict]],
    root: Path,
    uri,
    edits,
) -> None:
    if not isinstance(uri, str) or not isinstance(edits, list):
        raise ValueError("rename edit must contain a file URI and edit array")
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError("rename edits must use file URIs")
    path = Path(unquote(parsed.path)).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError("rename edit escapes the workspace") from None
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"rename target is not a regular file: {path}")
    if any(not isinstance(edit, dict) for edit in edits):
        raise ValueError("rename text edits must be objects")
    if not edits:
        return
    grouped.setdefault(path, []).extend(edits)


def _apply_text_edits(source: str, edits: list[dict]) -> str:
    resolved: list[tuple[int, int, str]] = []
    lines = source.splitlines(keepends=True)
    offsets = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    inserted_bytes = 0
    for edit in edits:
        range_value = edit.get("range")
        new_text = edit.get("newText")
        if not isinstance(range_value, dict) or not isinstance(new_text, str):
            raise ValueError("rename edit requires range and newText")
        inserted_bytes += len(new_text.encode("utf-8"))
        if inserted_bytes > MAX_WORKSPACE_EDIT_BYTES:
            raise ValueError("rename inserted text exceeds 8 MiB")
        start = _lsp_offset(source, range_value.get("start"), lines, offsets)
        end = _lsp_offset(source, range_value.get("end"), lines, offsets)
        if end < start:
            raise ValueError("rename edit range is reversed")
        resolved.append((start, end, new_text))
    resolved.sort(key=lambda item: (item[0], item[1]))
    previous_end = -1
    for start, end, _ in resolved:
        if start < previous_end:
            raise ValueError("rename edits overlap")
        previous_end = end
    updated = source
    for start, end, new_text in reversed(resolved):
        updated = updated[:start] + new_text + updated[end:]
    return updated


def _lsp_offset(
    source: str,
    position,
    lines: list[str] | None = None,
    offsets: list[int] | None = None,
) -> int:
    if not isinstance(position, dict):
        raise ValueError("rename edit position must be an object")
    line = position.get("line")
    character = position.get("character")
    if (
        isinstance(line, bool)
        or not isinstance(line, int)
        or line < 0
        or isinstance(character, bool)
        or not isinstance(character, int)
        or character < 0
    ):
        raise ValueError("rename edit position must be non-negative")
    if lines is None:
        lines = source.splitlines(keepends=True)
    if offsets is None:
        offsets = []
        offset = 0
        for value in lines:
            offsets.append(offset)
            offset += len(value)
    if line == len(lines) and character == 0:
        return len(source)
    if line >= len(lines):
        raise ValueError("rename edit line is outside the file")
    prefix = offsets[line]
    text = lines[line]
    units = 0
    for index, value in enumerate(text):
        if units == character:
            return prefix + index
        units += len(value.encode("utf-16-le")) // 2
        if units > character:
            raise ValueError("rename edit splits a UTF-16 character")
    if units == character:
        return prefix + len(text)
    raise ValueError("rename edit character is outside the line")


def _lsp_position(path: Path, position: dict) -> dict:
    """Translate MJJ's code-point column to the UTF-16 units LSP requires."""
    source = path.read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    if source.endswith(("\n", "\r")):
        lines.append("")
    line = position["line"]
    column = position["character"]
    if line >= len(lines) or column > len(lines[line]):
        raise ValueError("navigation position is outside the file")
    units = len(lines[line][:column].encode("utf-16-le")) // 2
    return {"line": line, "character": units}


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
