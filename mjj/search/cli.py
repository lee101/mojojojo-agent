"""First-class disk search CLI over the same hybrid index the agent uses."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .index import RepositoryIndex, SearchHit, build_index


def search(
    root: str | Path,
    query: str,
    *,
    path: str = "",
    mode: str = "auto",
    regex: bool = False,
    limit: int = 8,
    force: bool = False,
) -> tuple[RepositoryIndex, list[SearchHit]]:
    root_path = Path(root).expanduser().resolve()
    scope = ""
    if path:
        target = (
            (root_path / path).resolve()
            if not Path(path).is_absolute()
            else Path(path).resolve()
        )
        try:
            scope = target.relative_to(root_path).as_posix()
        except ValueError as exc:
            raise ValueError("search path must stay inside the root") from exc
        if not target.exists():
            raise ValueError(f"search path does not exist: {path}")
        if scope == ".":
            scope = ""
    index = build_index(root_path, force=force)
    hits = index.search(query, mode=mode, regex=regex, limit=limit, scope=scope)
    return index, hits


def _hit_json(hit: SearchHit) -> dict:
    return {
        "path": hit.chunk.path,
        "line": hit.line,
        "end_line": hit.chunk.end_line,
        "signature": hit.context or hit.chunk.signature,
        "score": round(hit.score, 6),
        "sources": list(hit.sources),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mjj-search", description="hybrid repository search")
    parser.add_argument("query")
    parser.add_argument("path", nargs="?", default="")
    parser.add_argument("--root", default=".")
    parser.add_argument("--mode", choices=["auto", "literal", "semantic"], default="auto")
    parser.add_argument("--regex", action="store_true")
    parser.add_argument("--limit", type=int, choices=range(1, 21), default=8, metavar="N")
    parser.add_argument("--force", action="store_true", help="rebuild every indexed file")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args(argv)
    try:
        index, hits = search(
            args.root,
            args.query,
            path=args.path,
            mode=args.mode,
            regex=args.regex,
            limit=args.limit,
            force=args.force,
        )
    except (OSError, ValueError) as exc:
        print(f"mjj-search: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([_hit_json(hit) for hit in hits], separators=(",", ":")))
    else:
        print(index.format_hits(hits))
    if args.stats:
        print(
            f"{index.stats.files} files · {index.stats.chunks} chunks · "
            f"{index.backend_name} · {index.stats.elapsed_seconds * 1000:.1f} ms",
            file=sys.stderr,
        )
    return 0 if hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
