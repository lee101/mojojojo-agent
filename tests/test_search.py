from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from mjj.ledger import Budget, Ledger, estimate_tokens
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
    assert len(semantic) == 2
    assert semantic[0].chunk.path == "app.py"
    assert "refresh_access_token" in semantic[0].chunk.signature

    # The same result shape is retained without rg.
    monkeypatch.setattr(index_module.shutil, "which", lambda _name: None)
    fallback = index.search("fetch_user", mode="literal", limit=2)
    assert fallback[0].line == literal[0].line


def test_exact_search_stops_at_score_cliff_and_uses_no_context(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)
    index = build_index(tmp_path)

    hits = index.search("refresh_access_token", limit=8)
    assert len(hits) == 1
    output = index.format_hits(hits)
    assert output == "app.py:7: def refresh_access_token(value):"
    assert "return value.strip()" not in output
    rg_output = "./app.py:7:def refresh_access_token(value):\n"
    assert estimate_tokens(output) <= estimate_tokens(rg_output)


def test_format_groups_repeated_file_hits(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    (tmp_path / "repeat.py").write_text(
        "def first():\n"
        "    return shared_value\n\n"
        "def second():\n"
        "    return shared_value\n",
        encoding="utf-8",
    )
    index = build_index(tmp_path)

    hits = index.search("shared_value", limit=8)
    assert len(hits) == 2
    output = index.format_hits(hits)
    assert output.count("repeat.py:") == 1
    assert "  2: return shared_value" in output
    assert "  5: return shared_value" in output
    rg_output = (
        "./repeat.py:2:    return shared_value\n"
        "./repeat.py:5:    return shared_value\n"
    )
    assert estimate_tokens(output) <= estimate_tokens(rg_output)


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


def test_auto_exact_search_skips_vector_scan(tmp_path: Path, monkeypatch) -> None:
    _write_fixture(tmp_path)
    index = build_index(tmp_path)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("decisive literal search should not scan vectors")

    monkeypatch.setattr(index.vectors, "search_text", unexpected)
    hits = index.search("refresh_access_token", mode="auto", limit=8)

    assert hits
    assert hits[0].sources == ("literal",)


def test_search_falls_back_to_ignored_and_large_text_only_after_a_miss(
    tmp_path: Path,
) -> None:
    (tmp_path / ".gitignore").write_text("ignored.py\nlarge.txt\n")
    (tmp_path / "normal.py").write_text("normal_token = True\n")
    (tmp_path / "ignored.py").write_text("fallback_secret_needle = True\n")
    (tmp_path / "binary.dat").write_bytes(b"fallback_secret_needle\0hidden")
    (tmp_path / "large.txt").write_text(
        "x" * (2 * 1024 * 1024 + 8) + "\noversize_quasar_needle\n"
    )
    context = ToolContext(tmp_path, Ledger())
    tool = SearchTool()

    normal = tool.run({"query": "normal_token"}, context)
    ignored = tool.run({"query": "fallback_secret_needle"}, context)
    large = tool.run({"query": "oversize_quasar_needle"}, context)

    assert "normal.py:1" in normal.output
    assert normal.meta["strategy"] == "literal"
    assert "ignored.py:1" in ignored.output
    assert "binary.dat" not in ignored.output
    assert ignored.meta["strategy"] == "fallback"
    assert "large.txt:2" in large.output
    assert large.meta["strategy"] == "fallback"


def test_search_cursor_pages_broad_results_and_caps_long_lines(tmp_path: Path) -> None:
    for number in range(12):
        (tmp_path / f"item_{number:02}.txt").write_text(
            f"shared_result {number} " + "z" * 2_000 + "\n"
        )
    context = ToolContext(tmp_path, Ledger())
    tool = SearchTool()

    first = tool.run({"query": "shared_result", "limit": 3}, context)
    second = tool.run(
        {"query": "shared_result", "limit": 3, "cursor": first.meta["next_cursor"]},
        context,
    )

    assert first.meta["next_cursor"] == 3
    assert "cursor 3" in first.output
    assert second.meta["cursor"] == 3
    assert first.output != second.output
    assert max(map(len, first.output.splitlines())) < 260


def test_small_budget_cursor_advances_only_past_fully_rendered_hits(tmp_path: Path) -> None:
    for number in range(20):
        (tmp_path / f"match_{number:02}.py").write_text(
            f"bounded_match = {number}\n"
        )
    ledger = Ledger(Budget(default=64, search=64))
    context = ToolContext(tmp_path, ledger)

    first = SearchTool().run(
        {"query": "bounded_match", "limit": 20}, context
    )

    assert not ledger.drops
    assert 0 < first.meta["hits"] < 20
    assert first.meta["next_cursor"] == first.meta["hits"]
    assert f"cursor {first.meta['hits']}" in first.output
