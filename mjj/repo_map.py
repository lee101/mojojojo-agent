"""Reference-weighted, token-bounded repository symbol maps."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .search.index import RepositoryIndex
from .search.lexical import tokenize


_DEFINITION = re.compile(
    r"""(?x)^\s*(?:
    (?:async\s+)?def\s+(?P<python_fn>[A-Za-z_]\w*)
    |class\s+(?P<python_class>[A-Za-z_]\w*)
    |(?:pub(?:lic)?\s+)?(?:async\s+)?fn\s+(?P<fn>[A-Za-z_]\w*)
    |(?:pub(?:lic)?\s+)?(?:struct|enum|trait|interface|record)\s+(?P<type>[A-Za-z_]\w*)
    |(?:export\s+)?(?:async\s+)?function\s+(?P<function>[A-Za-z_$][\w$]*)
    |(?:export\s+)?(?:type|interface)\s+(?P<ts_type>[A-Za-z_$][\w$]*)
    |func\s+(?:\([^)]*\)\s*)?(?P<go>[A-Za-z_]\w*)
    |\#{1,6}\s+(?P<heading>.+?)\s*$
    )"""
)
_GENERIC = {
    "main",
    "init",
    "test",
    "get",
    "set",
    "run",
    "new",
    "data",
    "value",
    "result",
}


@dataclass(frozen=True)
class Definition:
    path: str
    line: int
    name: str
    signature: str


@dataclass(frozen=True)
class RepoMap:
    output: str
    files: int
    symbols: int
    omitted_files: int


def render_repo_map(
    index: RepositoryIndex,
    *,
    scope: str = "",
    query: str = "",
    character_budget: int = 6_400,
) -> RepoMap:
    character_budget = max(0, character_budget)
    definitions: dict[str, list[Definition]] = defaultdict(list)
    for chunk in index.chunks:
        if not _in_scope(chunk.path, scope):
            continue
        match = _DEFINITION.match(chunk.signature)
        if match is None:
            continue
        if match.group("heading") and Path(chunk.path).suffix.lower() not in {
            ".md",
            ".mdx",
        }:
            continue
        name = next(value for value in match.groupdict().values() if value)
        definitions[chunk.path].append(
            Definition(
                path=chunk.path,
                line=chunk.start_line,
                name=name.strip(),
                signature=_short(chunk.signature),
            )
        )
    for path in index.files:
        if _in_scope(path, scope):
            definitions.setdefault(path, [])

    scores = {path: 0.1 + 1.0 / (1 + path.count("/")) for path in definitions}
    definers: dict[str, set[str]] = defaultdict(set)
    definition_frequencies: dict[str, int] = defaultdict(int)
    for path, items in definitions.items():
        for item in items:
            for term in _distinctive_terms(item.name):
                definers[term].add(path)
                definition_frequencies[term] += 1

    reference_scores: dict[str, float] = defaultdict(float)
    symbol_scores: dict[tuple[str, str], float] = defaultdict(float)
    for chunk in index.chunks:
        if not _in_scope(chunk.path, scope):
            continue
        for term, frequency in chunk.terms.items():
            targets = definers.get(term)
            if not targets:
                continue
            rarity = 1.0 / max(1, len(targets))
            weight = math.sqrt(min(frequency, 16)) * rarity
            for target in targets:
                if target == chunk.path:
                    continue
                reference_scores[target] += weight
                symbol_scores[(target, term)] += weight

    max_reference_score = max(reference_scores.values(), default=0.0)
    if max_reference_score:
        for path, score in reference_scores.items():
            scores[path] = scores.get(path, 0.1) + 20 * score / max_reference_score

    query_terms = set(tokenize(query))
    definition_count = sum(len(items) for items in definitions.values())
    query_weights = {
        term: 1.0
        + math.log1p(
            definition_count / max(1, definition_frequencies.get(term, 1))
        )
        for term in query_terms
    }
    if query.strip():
        for rank, hit in enumerate(index.search(query, limit=40, scope=scope), 1):
            scores[hit.chunk.path] = scores.get(hit.chunk.path, 0.1) + 100 / rank

    ranked_paths = sorted(
        definitions,
        key=lambda path: (-scores.get(path, 0.0), path),
    )
    title = "repository map"
    if scope:
        title += f" · {scope}"
    if query.strip():
        title += f" · query={_short(query, 80)}"
    if len(title) >= character_budget:
        return RepoMap(title[:character_budget], 0, 0, len(ranked_paths))

    blocks: list[str] = []
    included_symbols = 0
    for path in ranked_paths:
        items = sorted(
            definitions[path],
            key=lambda item: (
                -_symbol_relevance(item, query_weights, symbol_scores),
                item.line,
            ),
        )[:4]
        lines = [path]
        lines.extend(f"  {item.line}: {item.signature}" for item in items)
        block = "\n".join(lines)
        prospective_blocks = [*blocks, block]
        prospective_omitted = len(ranked_paths) - len(prospective_blocks)
        prospective = "\n".join([title, *prospective_blocks])
        if prospective_omitted:
            prospective += _footer(prospective_omitted)
        if len(prospective) > character_budget:
            break
        blocks.append(block)
        included_symbols += len(items)
    omitted = max(0, len(ranked_paths) - len(blocks))
    output = "\n".join([title, *blocks])
    if omitted:
        footer = _footer(omitted)
        if len(output) + len(footer) <= character_budget:
            output += footer
    return RepoMap(output, len(blocks), included_symbols, omitted)


def _distinctive_terms(name: str) -> set[str]:
    return {
        term
        for term in tokenize(name)
        if len(term) >= 4 and term not in _GENERIC and not term.isdigit()
    }


def _symbol_relevance(
    item: Definition,
    query_weights: dict[str, float],
    symbol_scores: dict[tuple[str, str], float],
) -> float:
    terms = _distinctive_terms(item.name)
    references = sum(
        symbol_scores.get((item.path, term), 0.0) for term in terms
    )
    # A task-specific name match must beat generic repository centrality. The
    # logarithm still provides useful ordering when no query term names a
    # symbol, without allowing a ubiquitous helper to swamp the user's intent.
    task_match = sum(query_weights[term] for term in terms & query_weights.keys())
    return 100 * task_match + math.log1p(references)


def _in_scope(path: str, scope: str) -> bool:
    return not scope or path == scope or path.startswith(scope.rstrip("/") + "/")


def _footer(omitted: int) -> str:
    return f"\n… {omitted} files omitted — narrow path or add a query …"


def _short(value: str, limit: int = 200) -> str:
    compact = " ".join(value.replace("\t", "    ").split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"
