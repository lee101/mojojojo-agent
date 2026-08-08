from __future__ import annotations

from pathlib import Path

import pytest

from mjj.repo_map import render_repo_map
from mjj.search.index import build_index
from mjj.symbols import extract_symbols


def test_extract_symbols_python_typed_signature(tmp_path: Path) -> None:
    path = tmp_path / "mod.py"
    path.write_text(
        "class Store:\n"
        "    def get(self, key: str) -> int:\n"
        "        return 1\n"
        "\n"
        "def search(query: str, limit: int = 20) -> list[str]:\n"
        "    return []\n"
    )
    symbols = extract_symbols(path)
    if symbols is None:
        pytest.skip("tree-sitter-language-pack unavailable")
    names = [item.name for item in symbols]
    assert names == ["Store", "get", "search"]
    search = next(item for item in symbols if item.name == "search")
    assert "query: str" in search.signature
    assert "limit: int" in search.signature
    assert search.line == 5


def test_repo_map_prefers_typed_tree_sitter_signatures(tmp_path: Path) -> None:
    (tmp_path / "api.py").write_text(
        "def refresh_access_token(user_id: str, *, force: bool = False) -> str:\n"
        "    return user_id\n"
    )
    index = build_index(tmp_path)
    repo_map = render_repo_map(index, query="refresh_access_token", character_budget=2_000)
    assert "api.py" in repo_map.output
    symbols = extract_symbols(tmp_path / "api.py")
    if symbols is None:
        pytest.skip("tree-sitter-language-pack unavailable")
    assert "user_id: str" in repo_map.output
    assert "force: bool" in repo_map.output
