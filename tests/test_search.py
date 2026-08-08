from __future__ import annotations

import math
import os
import re
import time
import zlib
from pathlib import Path

import pytest

from mjj.ledger import Budget, Ledger, estimate_tokens
from mjj.search import index as index_module
from mjj.search.index import MAGIC, RepositoryIndex, _make_chunks, build_index
from mjj.search.lexical import LexicalIndex, term_frequencies, tokenize
from mjj.search.vectors import (
    Int8Vectors,
    _hash64,
    _library_candidates,
    encode,
    encode_tokens,
    quantize,
    static_embedding,
)
from mjj.tools.base import ToolContext
from mjj.tools.search import SearchTool

_REFERENCE_WORDS = re.compile(r"[A-Za-z][A-Za-z0-9]*|\d+")
_REFERENCE_CAMEL_BOUNDARY = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)


def _reference_tokenize(value: str) -> list[str]:
    tokens: list[str] = []
    for match in _REFERENCE_WORDS.finditer(value):
        word = match.group(0)
        whole = word.lower()
        pieces = [piece.lower() for piece in _REFERENCE_CAMEL_BOUNDARY.split(word)]
        tokens.append(whole)
        if len(pieces) > 1:
            tokens.extend(piece for piece in pieces if piece != whole)
    return tokens


def _reference_hash64(value: str) -> int:
    encoded = value.encode("utf-8", "surrogatepass")
    return zlib.crc32(encoded) | (zlib.crc32(encoded, 0x9E3779B9) << 32)


def _reference_project(values: list[float], feature: str, weight: float) -> None:
    hashed = _reference_hash64(feature)
    values[hashed % len(values)] += weight if hashed & (1 << 63) else -weight


def _reference_static_embedding(text: str, dim: int) -> list[float]:
    values = [0.0] * dim
    frequencies: dict[str, int] = {}
    for token in _reference_tokenize(text):
        frequencies[token] = frequencies.get(token, 0) + 1
    for token, frequency in frequencies.items():
        token_weight = 1.5 * math.sqrt(frequency)
        _reference_project(values, "token:" + token, token_weight)
        marked = "^" + token + "$"
        for width, weight in ((3, 0.45), (4, 0.65)):
            if len(marked) < width:
                continue
            for offset in range(len(marked) - width + 1):
                _reference_project(
                    values,
                    f"ngram:{marked[offset:offset + width]}",
                    weight,
                )
    norm = math.sqrt(sum(value * value for value in values))
    if norm:
        inverse = 1.0 / norm
        values = [value * inverse for value in values]
    return values


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


def test_tokenize_matches_reference_order_and_duplicates() -> None:
    text = "lowercase lowercase 123 123 snake_case HTTPServer XMLHttpRequest"
    expected = [
        "lowercase",
        "lowercase",
        "123",
        "123",
        "snake",
        "case",
        "httpserver",
        "http",
        "server",
        "xmlhttprequest",
        "xml",
        "http",
        "request",
    ]
    assert _reference_tokenize(text) == expected
    assert tokenize(text) == expected


def test_native_tokenize_and_embed_match_python_when_abi_present() -> None:
    from mjj.search.lexical import tokenize_python
    from mjj.search.vectors import (
        native_static_embedding_tokens,
        native_static_embedding_tokens_batch,
        native_tokenize,
        static_embedding_tokens_python,
        _default_backend,
    )

    backend = _default_backend()
    if not backend.tokenize_available or not backend.embed_available:
        pytest.skip("Mojo search ABI with tokenize/embed not loaded")
    texts = (
        "",
        "x A HTTP 123 !!!",
        "lowercase lowercase 123 123 snake_case HTTPServer XMLHttpRequest",
        "version2HTTPServer path/to/file.py CAPS lowerUPPER42Next",
    )
    bags = [tokenize_python(text) for text in texts]
    for text, tokens in zip(texts, bags):
        assert native_tokenize(text) == tokenize_python(text)
        assert native_static_embedding_tokens(tokens, 256) == (
            static_embedding_tokens_python(tokens, 256)
        )
    if backend.embed_batch_available:
        batch = native_static_embedding_tokens_batch(bags, 256)
        assert batch is not None
        assert batch == [
            static_embedding_tokens_python(tokens, 256) for tokens in bags
        ]


def test_mjj_accel_disables_native_tokenize_and_embed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mjj.search import vectors as vector_module
    from mjj.search.lexical import tokenize_python

    monkeypatch.setenv("MJJ_ACCEL", "0")
    vector_module._BACKEND = None
    assert vector_module.native_tokenize("HTTPServer") is None
    assert vector_module.native_static_embedding_tokens(["http"], 8) is None
    assert vector_module.native_static_embedding_tokens_batch([["http"]], 8) is None
    assert tokenize("HTTPServer") == tokenize_python("HTTPServer")
    vector_module._BACKEND = None


def test_static_embedding_tokens_batch_matches_singles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mjj.search.lexical import tokenize_python
    from mjj.search.vectors import (
        static_embedding_tokens,
        static_embedding_tokens_batch,
        static_embedding_tokens_python,
    )

    bags = [
        tokenize_python("HTTPServer.fetch_user"),
        tokenize_python("xmlhttprequest path/to/file.py"),
        [],
    ]
    assert static_embedding_tokens_batch(bags, 256) == [
        static_embedding_tokens(bag, 256) for bag in bags
    ]
    monkeypatch.setenv("MJJ_ACCEL", "0")
    from mjj.search import vectors as vector_module

    vector_module._BACKEND = None
    assert static_embedding_tokens_batch(bags, 64) == [
        static_embedding_tokens_python(bag, 64) for bag in bags
    ]
    vector_module._BACKEND = None


@pytest.mark.parametrize("dim", (1, 7, 256))
@pytest.mark.parametrize(
    "text",
    (
        "",
        "x A HTTP 123 !!!",
        "lowercase lowercase 123 123 snake_case HTTPServer XMLHttpRequest",
        "version2HTTPServer path/to/file.py CAPS lowerUPPER42Next",
    ),
)
def test_static_embedding_matches_old_feature_hashing_exactly(
    dim: int,
    text: str,
) -> None:
    reference = _reference_static_embedding(text, dim)
    assert static_embedding(text, dim) == reference
    assert encode(text, dim) == quantize(reference)
    assert _hash64("ngram:^HTT") == _reference_hash64("ngram:^HTT")


def test_index_reuses_tokens_without_changing_terms_or_vectors() -> None:
    path = "src/HTTPServer.py"
    text = (
        "def refresh_access_token(worker_id):\n"
        "    return workerBootstrap and cached_result\n\n"
        "class XMLHttpRequest:\n"
        "    pass\n"
    )

    for chunk, body, embedding_tokens in _make_chunks(path, text):
        assert chunk.terms == term_frequencies(
            body,
            path=path,
            signature=chunk.signature,
        )
        assert encode_tokens(embedding_tokens) == encode(
            path + "\n" + chunk.signature + "\n" + body
        )


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


def test_repository_mojo_search_build_is_preferred_to_optional_peer() -> None:
    candidates = [Path(path).as_posix() for path in _library_candidates()]
    local = next(
        i
        for i, path in enumerate(candidates)
        if "/build/libmjj_search.so" in path
    )
    peer = next(i for i, path in enumerate(candidates) if "/mojo-embed/build/" in path)

    assert local < peer


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


def test_search_treats_blank_path_as_workspace_root(tmp_path: Path) -> None:
    _write_fixture(tmp_path)

    result = SearchTool().run(
        {"query": "user", "path": ""},
        ToolContext(cwd=tmp_path, ledger=Ledger()),
    )

    assert result.ok
    assert "app.py:" in result.output


def test_auto_exact_search_skips_vector_scan(tmp_path: Path, monkeypatch) -> None:
    _write_fixture(tmp_path)
    index = build_index(tmp_path)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("decisive literal search should not scan vectors")

    monkeypatch.setattr(index.vectors, "search_text", unexpected)
    hits = index.search("refresh_access_token", mode="auto", limit=8)

    assert hits
    assert hits[0].sources == ("literal",)


def test_literal_rg_accepts_windows_relative_paths(
    tmp_path: Path, monkeypatch
) -> None:
    _write_fixture(tmp_path)
    index = build_index(tmp_path)
    payload = (
        '{"type":"match","data":{"path":{"text":".\\\\app.py"},'
        '"lines":{"text":"def refresh_access_token(value):\\n"},'
        '"line_number":7,"submatches":[{"start":4,"end":25}]}}\n'
    )

    class FakeStdout:
        def __init__(self, lines) -> None:
            self._lines = iter(lines)

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._lines)

        def close(self) -> None:
            return None

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = FakeStdout([payload])

        def kill(self) -> None:
            return None

        def wait(self, timeout=None) -> int:
            return 0

    monkeypatch.setattr(index_module.shutil, "which", lambda _name: "rg")
    monkeypatch.setattr(
        index_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )

    hits = index.search("refresh_access_token", mode="literal", limit=4)

    assert hits
    assert hits[0].chunk.path == "app.py"
    assert hits[0].line == 7
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
