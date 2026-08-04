"""Token-bounded hybrid repository search tool."""

from __future__ import annotations

from pathlib import Path

from ..search.index import RepositoryIndex, build_index
from .base import ToolContext, ToolResult


class SearchTool:
    name = "search"
    description = "Ranked code search: literal, identifiers, and naming variants."
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Text or regular expression to find.",
            },
            "mode": {
                "type": "string",
                "enum": ["auto", "literal", "semantic"],
                "description": "auto fuses all signals; semantic finds naming variants.",
            },
            "path": {
                "type": "string",
                "description": "Optional file or directory inside the workspace.",
            },
            "regex": {
                "type": "boolean",
                "description": "Interpret query as a regular expression.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": (
                    "Maximum results; a strong score cliff may return fewer."
                ),
            },
            "cursor": {
                "type": "integer",
                "minimum": 0,
                "maximum": 40,
                "description": "Offset returned by a previous broad search.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return self._error(ctx, "query must be a non-empty string")
        mode = args.get("mode", "auto")
        if mode not in {"auto", "literal", "semantic"}:
            return self._error(ctx, "mode must be auto, literal, or semantic")
        regex = args.get("regex", False)
        if not isinstance(regex, bool):
            return self._error(ctx, "regex must be true or false")
        limit_value = args.get("limit", 8)
        if isinstance(limit_value, bool):
            return self._error(ctx, "limit must be an integer")
        try:
            limit = int(limit_value)
        except (TypeError, ValueError):
            return self._error(ctx, "limit must be an integer")
        if not 1 <= limit <= 20:
            return self._error(ctx, "limit must be between 1 and 20")
        cursor_value = args.get("cursor", 0)
        if isinstance(cursor_value, bool):
            return self._error(ctx, "cursor must be an integer")
        try:
            cursor = int(cursor_value)
        except (TypeError, ValueError):
            return self._error(ctx, "cursor must be an integer")
        if not 0 <= cursor <= 40:
            return self._error(ctx, "cursor must be between 0 and 40")

        root = ctx.cwd.resolve()
        scope = ""
        requested_path = args.get("path")
        if requested_path is not None:
            if not isinstance(requested_path, str) or not requested_path:
                return self._error(ctx, "path must be a non-empty string")
            target = ctx.resolve(requested_path)
            try:
                scope = target.relative_to(root).as_posix()
            except ValueError:
                return self._error(
                    ctx, "search path must stay inside the workspace"
                )
            if not target.exists():
                return self._error(ctx, f"path does not exist: {requested_path}")
            if scope == ".":
                scope = ""

        try:
            cache_key = f"search-index:{root}"
            cached = ctx.state.get(cache_key)
            index = build_index(
                root,
                existing=cached if isinstance(cached, RepositoryIndex) else None,
            )
            ctx.state[cache_key] = index
            all_hits = index.search(
                query,
                mode=mode,
                regex=regex,
                limit=min(50, cursor + limit + 1),
                scope=scope,
            )
            fallback = False
            if not all_hits:
                all_hits = index.fallback_search(
                    query,
                    regex=regex,
                    limit=min(50, cursor + limit + 1),
                    scope=scope,
                )
                fallback = bool(all_hits)
            candidates = all_hits[cursor : cursor + limit]
            if cursor and not candidates:
                hits = []
                output = "no more matches"
            else:
                hits, output, next_cursor = _fit_page(
                    index,
                    candidates,
                    cursor=cursor,
                    total_available=len(all_hits),
                    character_budget=ctx.ledger.budget.for_tool("search") * 4,
                )
            if not candidates:
                next_cursor = None
        except (OSError, ValueError) as exc:
            return self._error(ctx, str(exc))
        clipped = ctx.ledger.clip(
            "search",
            output,
            hint="narrow path or lower limit",
        )
        return ToolResult(
            output=clipped,
            meta={
                "hits": len(hits),
                "cursor": cursor,
                "next_cursor": next_cursor,
                "mode": mode,
                "strategy": (
                    "fallback"
                    if fallback
                    else "+".join(
                        sorted({source for hit in hits for source in hit.sources})
                    )
                    or mode
                ),
                "backend": index.backend_name,
                "indexed_files": index.stats.files,
                "indexed_chunks": index.stats.chunks,
                "index_ms": round(index.stats.elapsed_seconds * 1000, 2),
                "incremental": not index.stats.wrote_index,
            },
        )

    @staticmethod
    def _error(ctx: ToolContext, message: str) -> ToolResult:
        return ToolResult.error(ctx.ledger.clip("search", message))


def _fit_page(
    index: RepositoryIndex,
    candidates,
    *,
    cursor: int,
    total_available: int,
    character_budget: int,
):
    """Fit whole ranked hits so a continuation never skips clipped matches."""
    if not candidates:
        return [], "no matches", None
    for count in range(len(candidates), 0, -1):
        hits = candidates[:count]
        next_cursor = cursor + count if total_available > cursor + count else None
        output = index.format_hits(hits)
        if next_cursor is not None:
            output += (
                f"\n… more matches — repeat this search with cursor "
                f"{next_cursor} …"
            )
        if len(output) <= character_budget or count == 1:
            return hits, output, next_cursor
    raise AssertionError("unreachable")


TOOLS = [SearchTool()]
