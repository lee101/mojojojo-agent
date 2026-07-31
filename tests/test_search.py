from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from mjj.ledger import Budget, Ledger
from mjj.search import index as index_module
from mjj.search.index import MAGIC, RepositoryIndex, build_index
from mjj.search.lexical import LexicalIndex, term_frequencies, tokenize
from mjj.search.vectors import Int8Vectors, encode
from mjj.tools.base import ToolContext
from mjj.tools.search import SearchTool


def test_tokenize_identifiers_and_bm25() -> None:
    tokens = tokenize("HTTPServer.fetch_user src/auth-token.py")
    assert {"httpserver", "http", "server", "fetch", "user", "auth", "token"} <= set(
        tokens
    )
    documents = [
        term_frequencies("def refresh_access_token(): pass", path="auth.py"),
        term_frequencies("def render_button(): pass", path="ui.py"),
    ]
    hits = LexicalIndex(documents).search("accessToken")
    assert hits and hits[0][0] == 0


def test_python_vector_scan_finds_naming_variant() -> None:
    rows = []
    factors = []
    for text in ("refresh_access_token", "render navigation button"):
        row, factor = encode(text)
        rows.append(row)
        factors.append(factor)

    class MissingBackend:
        available = False

    matrix = Int8Vectors(
        b"".join(rows), factors, backend=MissingBackend()  # type: ignore[arg-type]
    )
    hits = matrix.search_text("refreshAccessToken", 2)
    assert hits[0][0] == 0
    assert matrix.backend_name == "python"


def _write_fixture(root: Path) -> None:
    (root / ".gitignore").write_text(
        "ignored.py\ngenerated/\n*.min.js\n", encoding="utf-8"
    )
    (root / "app.py").write_text(
        '"""Users."""\n\n'
        "class UserStore:\n"
        "    def fetch_user(self, user_id):\n"
        "        return self.cache[user_id]\n\n"
        "def refresh_access_token(value):\n"
        "    return value.strip()\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# Search fixture\n\n"
        "Intro text.\n\n"
        "## Authentication\n\n"
        "Refresh credentials safely.\n",
        encoding="utf-8",
    )
    (root / "ignored.py").write_text("secret = True\n", encoding="utf-8")
    (root / "binary.dat").write_bytes(b"code\0binary")
    (root / "generated").mkdir()
    (root / "generated" / "output.py").write_text("generated = True\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "package.js").write_text("ignored = true;\n")


def test_build_persist_chunk_incremental_and_ignore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_fixture(tmp_path)
    # Exercise the stdlib walker, including its .gitignore implementation.
    monkeypatch.setattr(index_module.shutil, "which", lambda _name: None)
    built = build_index(tmp_path)

    assert built.index_path.read_bytes().startswith(MAGIC)
    assert "app.py" in built.files
    assert "ignored.py" not in built.files
    assert "generated/output.py" not in built.files
    assert "node_modules/package.js" not in built.files
    assert all(chunk.path != "binary.dat" for chunk in built.chunks)
    signatures = {chunk.signature for chunk in built.chunks}
    assert "class UserStore:" in signatures
    assert "def refresh_access_token(value):" in signatures
    assert "## Authentication" in signatures
    assert built.vectors.count == len(built.chunks)

    reopened = RepositoryIndex.open(tmp_path)
    assert [chunk.signature for chunk in reopened.chunks] == [
        chunk.signature for chunk in built.chunks
    ]
    index_mtime = built.index_path.stat().st_mtime_ns
    unchanged = build_index(tmp_path)
    assert unchanged.stats.changed_files == 0
    assert unchanged.stats.wrote_index is False
    assert unchanged.index_path.stat().st_mtime_ns == index_mtime
    cached = build_index(tmp_path, existing=unchanged)
    assert cached is unchanged

    time.sleep(0.002)
    with (tmp_path / "app.py").open("a", encoding="utf-8") as output:
        output.write("\ndef delete_user(user_id):\n    return user_id\n")
    os.utime(tmp_path / "app.py", None)
    changed = build_index(tmp_path)
    assert changed.stats.changed_files == 1
    assert changed.stats.wrote_index is True
    assert any("delete_user" in chunk.signature for chunk in changed.chunks)


def test_literal_regex_and_semantic_are_one_ranked_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_fixture(tmp_path)
    index = build_index(tmp_path)
    literal = index.search("fetch_user", mode="literal", limit=4)
    assert literal
    assert literal[0].chunk.path == "app.py"
    assert literal[0].line == 4
    assert literal[0].sources == ("literal",)

    regex = index.search(r"refresh_(access|auth)_token", regex=True, limit=4)
    assert regex and regex[0].line == 7
    with pytest.raises(ValueError, match="invalid regular expression"):
        index.search("(", regex=True)

    semantic = index.search("refreshAccessToken", mode="semantic", limit=2)
    assert semantic[0].chunk.path == "app.py"
    assert "refresh_access_token" in semantic[0].chunk.signature

    # The same result shape is retained without rg.
    monkeypatch.setattr(index_module.shutil, "which", lambda _name: None)
    fallback = index.search("fetch_user", mode="literal", limit=2)
    assert fallback[0].line == literal[0].line


def test_search_tool_clips_once_and_returns_addresses(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    ledger = Ledger(
        budget=Budget(default=80, shell=80, read=80, search=80, py=80)
    )
    context = ToolContext(cwd=tmp_path, ledger=ledger)
    result = SearchTool().run(
        {"query": "user", "mode": "auto", "limit": 10}, context
    )
    assert result.ok
    assert "app.py:" in result.output
    assert len(result.output) <= 80 * 4 + 20
    assert ledger.tool_calls == 1
    assert result.meta["indexed_chunks"] >= 3
    assert result.meta["backend"] in {"mojo-embed", "python"}


def test_search_tool_rejects_workspace_escape(tmp_path: Path) -> None:
    ledger = Ledger()
    result = SearchTool().run(
        {"query": "x", "path": "../outside"},
        ToolContext(cwd=tmp_path, ledger=ledger),
    )
    assert not result.ok
    assert "inside the workspace" in result.output
    assert ledger.tool_calls == 1
