"""A search that always returns something is worse than one that says no.

Our vectors are hashed static embeddings, so similarity alone cannot tell a
nonsense query from a real conceptual one — on a large corpus the nonsense
query actually scores higher. Word overlap can tell them apart, and these tests
pin that: no shared word, no hit.
"""

from __future__ import annotations

import json

from mjj.ledger import Ledger
from mjj.tools import build_registry
from mjj.tools.base import ToolContext


def search(tmp_path, query: str, **extra) -> str:
    registry = build_registry(only=["search"])
    ctx = ToolContext(cwd=tmp_path, ledger=Ledger())
    result = registry.dispatch("search", json.dumps({"query": query, **extra}), ctx)
    assert result.ok, result.output
    return result.output


def make_repo(tmp_path):
    (tmp_path / "billing.py").write_text(
        "def worker_bootstrap(node):\n"
        "    '''Prepare a rented worker.'''\n"
        "    return node\n"
    )
    (tmp_path / "notes.md").write_text("# notes\nnothing relevant here\n")
    return tmp_path


def test_nonsense_query_returns_no_matches(tmp_path):
    make_repo(tmp_path)
    assert search(tmp_path, "zzzqqq_nonexistent") == "no matches"


def test_unrelated_concept_returns_no_matches(tmp_path):
    make_repo(tmp_path)
    assert search(tmp_path, "kubernetes ingress controller") == "no matches"


def test_absent_declaration_is_not_answered_with_the_nearest_chunk(tmp_path):
    make_repo(tmp_path)
    assert search(tmp_path, "def rolling_sharpe") == "no matches"


def test_naming_variants_still_match(tmp_path):
    make_repo(tmp_path)
    # camelCase query, snake_case definition: the whole reason the vector side
    # exists. Grounding must not cost us this.
    assert "billing.py" in search(tmp_path, "workerBootstrap")


def test_literal_hit_is_unaffected(tmp_path):
    make_repo(tmp_path)
    assert "billing.py:1" in search(tmp_path, "worker_bootstrap")


def test_empty_repository_says_no_matches(tmp_path):
    assert search(tmp_path, "anything") == "no matches"
