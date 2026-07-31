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
            hits = index.search(
                query,
                mode=mode,
                regex=regex,
                limit=limit,
                scope=scope,
            )
            output = index.format_hits(hits)
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
                "mode": mode,
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


TOOLS = [SearchTool()]
