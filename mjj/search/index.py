"""Persistent chunk index and fused repository search.

The on-disk format is deliberately simple: a fixed little-endian header,
compact JSON metadata, padding to a 64-byte boundary, contiguous int8 rows,
then one float32 factor per row.  The vector regions are scanned directly from
an mmap by mojo-embed when its shared library is available.
"""

from __future__ import annotations

import argparse
import bisect
import fnmatch
import json
import mmap
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

from .lexical import LexicalIndex, term_frequencies, tokenize
from .vectors import DIMENSION, Int8Vectors, encode


MAGIC = b"MJJIDX01"
VERSION = 1
HEADER = struct.Struct("<8sIIIIQ")
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FALLBACK_FILE_BYTES = 32 * 1024 * 1024
MAX_FALLBACK_FILES = 20_000
MAX_CHUNK_LINES = 120
LITERAL_CANDIDATES = 240
CONFIDENT_SCORE = 0.20
GENERIC_TERMS = frozenset(
    """
    def class func fn function struct enum trait impl interface type var let
    const return if else for while do switch case break continue import from
    package use pub public private static async await new self this null nil
    none true false int str string bool float double void error err test
    the a an of to in on at is are was were be been it its and or not with
    that this these those what how does do why when where which who
    """.split()
)
SCORE_CLIFF_RATIO = 0.60
SCORE_CLIFF_WINDOW = 3
_HARD_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mjj",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
    "target",
    "out",
    "__pycache__",
}
_BINARY_SUFFIXES = {
    ".7z", ".a", ".avi", ".bmp", ".class", ".dll", ".dylib", ".exe",
    ".gif", ".gz", ".ico", ".jar", ".jpeg", ".jpg", ".lockb", ".mov",
    ".mp3", ".mp4", ".o", ".pdf", ".png", ".pyc", ".so", ".tar",
    ".ttf", ".wav", ".webp", ".woff", ".woff2", ".xz", ".zip",
}
_DECLARATION = re.compile(
    r"""(?x)
    ^\s*(
        \#{1,6}\s+\S
        |(?:async\s+)?def\s+\w+
        |class\s+\w+
        |(?:pub(?:lic)?\s+)?(?:async\s+)?fn\s+\w+
        |func\s+(?:\([^)]*\)\s*)?\w+
        |(?:export\s+)?function\s+\w+
        |(?:pub(?:lic)?\s+)?(?:struct|enum|trait|interface|record)\s+\w+
        |impl(?:\s*<[^>]+>)?\s+\S+
        |(?:pub(?:lic)?\s+)?type\s+\w+
        |(?:describe|context|it|test)\s*\(
    )
    """
)


@dataclass
class Chunk:
    path: str
    start_line: int
    end_line: int
    signature: str
    terms: dict[str, int]

    def to_record(self) -> dict:
        return {
            "p": self.path,
            "s": self.start_line,
            "e": self.end_line,
            "g": self.signature,
            "t": self.terms,
        }

    @classmethod
    def from_record(cls, record: Mapping) -> "Chunk":
        return cls(
            path=str(record["p"]),
            start_line=int(record["s"]),
            end_line=int(record["e"]),
            signature=str(record["g"]),
            terms={
                str(term): int(frequency)
                for term, frequency in record["t"].items()
            },
        )


@dataclass
class SearchHit:
    chunk: Chunk
    line: int
    score: float
    sources: tuple[str, ...]
    context: str = ""


@dataclass
class UpdateStats:
    files: int = 0
    chunks: int = 0
    changed_files: int = 0
    reused_files: int = 0
    elapsed_seconds: float = 0.0
    wrote_index: bool = False


@dataclass
class RepositoryIndex:
    root: Path
    index_path: Path
    files: dict[str, tuple[int, int]]
    chunks: list[Chunk]
    vectors: Int8Vectors
    stats: UpdateStats = field(default_factory=UpdateStats)
    _mapping: mmap.mmap | bytes | None = field(default=None, repr=False)
    _lexical: LexicalIndex | None = field(default=None, init=False, repr=False)
    _regions: dict[str, tuple[list[int], list[int]]] | None = field(
        default=None, init=False, repr=False
    )
    _query_term_cache: dict[str, set[str]] = field(
        default_factory=dict, init=False, repr=False
    )
    _chunk_term_cache: dict[int, set[str]] = field(
        default_factory=dict, init=False, repr=False
    )

    @classmethod
    def open(
        cls,
        root: str | Path,
        index_path: str | Path | None = None,
    ) -> "RepositoryIndex":
        root_path = Path(root).resolve()
        destination = (
            Path(index_path).resolve()
            if index_path is not None
            else root_path / ".mjj" / "index"
        )
        if os.name == "nt":
            # Windows prevents replacing or deleting a memory-mapped file.
            # Keep immutable bytes as the backing store so incremental rebuilds
            # and temporary-directory cleanup remain possible.
            mapping: mmap.mmap | bytes = destination.read_bytes()
        else:
            file_handle = destination.open("rb")
            try:
                mapping = mmap.mmap(
                    file_handle.fileno(), 0, access=mmap.ACCESS_COPY
                )
            finally:
                file_handle.close()
        try:
            if len(mapping) < HEADER.size:
                raise ValueError("index header is truncated")
            magic, version, dim, count, metadata_size, data_offset = (
                HEADER.unpack_from(mapping)
            )
            if magic != MAGIC or version != VERSION:
                raise ValueError("unsupported search index format")
            if dim != DIMENSION:
                raise ValueError("search index has an unsupported vector dimension")
            metadata_end = HEADER.size + metadata_size
            if metadata_end > data_offset:
                raise ValueError("invalid search index offsets")
            required_size = data_offset + count * dim + count * 4
            if required_size > len(mapping):
                raise ValueError("search index data is truncated")
            metadata = json.loads(
                bytes(mapping[HEADER.size:metadata_end]).decode("utf-8")
            )
            recorded_root = Path(metadata["root"])
            if recorded_root != root_path:
                raise ValueError(
                    f"index belongs to {recorded_root}, not {root_path}"
                )
            chunks = [
                Chunk.from_record(record) for record in metadata["chunks"]
            ]
            if len(chunks) != count:
                raise ValueError("chunk and vector counts disagree")
            files = {
                str(path): (int(values[0]), int(values[1]))
                for path, values in metadata["files"].items()
            }
            vector_end = data_offset + count * dim
            data = memoryview(mapping)[data_offset:vector_end]
            if sys.byteorder == "little":
                factors = memoryview(mapping)[vector_end:required_size].cast("f")
            else:
                factor_array = array("f")
                factor_array.frombytes(bytes(mapping[vector_end:required_size]))
                factor_array.byteswap()
                factors = factor_array
            vectors = Int8Vectors(data, factors, dim=dim)
            return cls(
                root=root_path,
                index_path=destination,
                files=files,
                chunks=chunks,
                vectors=vectors,
                stats=UpdateStats(files=len(files), chunks=len(chunks)),
                _mapping=mapping,
            )
        except Exception:
            if isinstance(mapping, mmap.mmap):
                mapping.close()
            raise

    @property
    def backend_name(self) -> str:
        return self.vectors.backend_name

    def lexical(self) -> LexicalIndex:
        if self._lexical is None:
            self._lexical = LexicalIndex([chunk.terms for chunk in self.chunks])
        return self._lexical

    def _region_rows(self) -> dict[str, tuple[list[int], list[int]]]:
        if self._regions is None:
            grouped: dict[str, list[tuple[int, int]]] = {}
            for row, chunk in enumerate(self.chunks):
                grouped.setdefault(chunk.path, []).append(
                    (chunk.start_line, row)
                )
            self._regions = {
                path: (
                    [item[0] for item in sorted(items)],
                    [item[1] for item in sorted(items)],
                )
                for path, items in grouped.items()
            }
        return self._regions

    def row_for_line(self, path: str, line: int) -> int | None:
        region = self._region_rows().get(path)
        if not region:
            return None
        starts, rows = region
        position = bisect.bisect_right(starts, line) - 1
        if position < 0:
            return None
        row = rows[position]
        chunk = self.chunks[row]
        return row if line <= chunk.end_line else None

    def search(
        self,
        query: str,
        *,
        mode: str = "auto",
        regex: bool = False,
        limit: int = 10,
        scope: str = "",
    ) -> list[SearchHit]:
        if not query:
            return []
        if mode not in {"auto", "literal", "semantic"}:
            raise ValueError("mode must be auto, literal, or semantic")
        if regex:
            try:
                re.compile(query)
            except re.error as exc:
                raise ValueError(f"invalid regular expression: {exc}") from exc
        wanted = min(max(1, int(limit)), 50)
        candidate_count = min(max(wanted * 8, 24), 240)
        scope = _normalise_scope(scope)
        literal: dict[int, tuple[float, int, str]] = {}
        lexical: list[tuple[int, float]] = []
        semantic: list[tuple[int, float]] = []
        if mode in {"auto", "literal"}:
            literal = self._literal(query, regex, scope)
        # A bounded literal set is already the strongest possible answer. Do
        # not pay for BM25 and a complete vector scan just to confirm it. A
        # saturated literal set still uses fusion so broad queries are ranked.
        decisive_literal = (
            mode == "auto" and 0 < len(literal) <= wanted
        )
        if mode == "auto" and not decisive_literal:
            lexical = [
                item for item in self.lexical().search(query, candidate_count)
                if _in_scope(self.chunks[item[0]].path, scope)
                and self._grounded(item[0], query)
            ]
        if mode == "semantic" or mode == "auto" and not decisive_literal:
            semantic = [
                item
                for item in self.vectors.search_text(query, candidate_count)
                if _in_scope(self.chunks[item[0]].path, scope)
                and self._grounded(item[0], query)
            ]

        fused: dict[int, float] = {}
        sources: dict[int, set[str]] = {}
        raw_tiebreak: dict[int, float] = {}
        ranked_literal = sorted(
            literal.items(),
            key=lambda item: (-item[1][0], item[0]),
        )
        for name, ranked, weight in (
            ("literal", ranked_literal, 4.0),
            ("lexical", lexical, 2.0),
            ("semantic", semantic, 1.25),
        ):
            for rank, item in enumerate(ranked, 1):
                row = item[0]
                fused[row] = fused.get(row, 0.0) + weight / (20.0 + rank)
                sources.setdefault(row, set()).add(name)
                raw_score = item[1][0] if name == "literal" else item[1]
                raw_tiebreak[row] = raw_tiebreak.get(row, 0.0) + float(
                    raw_score
                ) * weight
        ordered = sorted(
            fused,
            key=lambda row: (-fused[row], -raw_tiebreak[row], row),
        )
        hits: list[SearchHit] = []
        for row in ordered:
            chunk = self.chunks[row]
            literal_detail = literal.get(row)
            line = literal_detail[1] if literal_detail else chunk.start_line
            context = literal_detail[2] if literal_detail else ""
            if any(
                hit.chunk.path == chunk.path
                and not (
                    chunk.end_line < hit.chunk.start_line
                    or chunk.start_line > hit.chunk.end_line
                )
                for hit in hits
            ):
                continue
            hits.append(
                SearchHit(
                    chunk=chunk,
                    line=line,
                    score=fused[row],
                    sources=tuple(sorted(sources[row])),
                    context=context,
                )
            )
            if len(hits) >= wanted:
                break
        return hits[:_adaptive_count(hits)]

    def fallback_search(
        self,
        query: str,
        *,
        regex: bool = False,
        limit: int = 10,
        scope: str = "",
    ) -> list[SearchHit]:
        """Last-resort literal/naming search of excluded text files.

        Normal search never indexes ignored files or files above 2 MiB. This
        tier is intentionally called only after that search misses. It streams
        at most 32 MiB per file, refuses binary data, retains hard directory
        exclusions unless explicitly scoped, and never emits an unbounded
        source line.
        """
        if not query:
            return []
        expression = re.compile(query if regex else re.escape(query))
        wanted = min(max(1, int(limit)), 50)
        normalised_query = re.sub(r"[^a-z0-9]", "", query.lower())
        candidates: list[tuple[float, str, int, str, str]] = []
        for relative, size in _fallback_files(self.root, self.files, scope):
            path = self.root / relative
            try:
                with path.open("rb") as raw:
                    if b"\0" in raw.read(8192):
                        continue
                with path.open(
                    "r", encoding="utf-8", errors="replace"
                ) as source:
                    for line_number, line in enumerate(source, 1):
                        exact = list(expression.finditer(line))
                        source_name = (
                            "fallback-large"
                            if size > MAX_FILE_BYTES
                            else "fallback-ignored"
                        )
                        if exact:
                            score = 1.0 + min(len(exact), 8) / 10.0
                        elif (
                            not regex
                            and len(normalised_query) >= 4
                            and len(line) <= 16_384
                            and normalised_query
                            in re.sub(r"[^a-z0-9]", "", line.lower())
                        ):
                            score = 0.5
                            source_name += "-naming"
                        else:
                            continue
                        candidates.append(
                            (
                                score,
                                relative,
                                line_number,
                                _short(line),
                                source_name,
                            )
                        )
                        if len(candidates) >= LITERAL_CANDIDATES:
                            break
            except (OSError, UnicodeError):
                continue
            if len(candidates) >= LITERAL_CANDIDATES:
                break
        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        return [
            SearchHit(
                chunk=Chunk(
                    path=path,
                    start_line=line,
                    end_line=line,
                    signature=context,
                    terms={},
                ),
                line=line,
                score=score,
                sources=(source,),
                context=context,
            )
            for score, path, line, context, source in candidates[:wanted]
        ]

    def _grounded(self, row: int, query: str) -> bool:
        """Does this chunk share any word with the query?

        Our vectors are hashed static embeddings, not a trained model: on a
        large corpus a nonsense query still scores ~0.31 while a genuine
        conceptual query scores ~0.27, so no similarity threshold can separate
        them. Word overlap can. ``worker_bootstrap`` and ``workerBootstrap``
        tokenise to the same two words, which is the naming-variant recall we
        actually want; ``zzzqqq_nonexistent`` shares nothing with anything and
        must return no matches rather than the nearest arbitrary chunk.
        """
        terms = self._query_terms(query)
        if not terms:
            return True
        return bool(terms & self._chunk_terms(row))

    # Words that carry no location information: language keywords the whole
    # tree shares, and English glue. Overlap on these is not evidence.

    def _query_terms(self, query: str) -> set[str]:
        """The query's *distinctive* words.

        ``def rolling_sharpe`` is grounded by ``rolling`` and ``sharpe``, never
        by ``def`` — otherwise one keyword in the query makes every function in
        the tree a match.
        """
        cached = self._query_term_cache.get(query)
        if cached is None:
            terms = {term for term in tokenize(query) if len(term) > 1}
            distinctive = terms - GENERIC_TERMS
            # A query made entirely of generic words has nothing to ground on;
            # let the ranking decide rather than refusing to answer.
            cached = distinctive or set()
            self._query_term_cache[query] = cached
        return cached

    def _chunk_terms(self, row: int) -> set[str]:
        cached = self._chunk_term_cache.get(row)
        if cached is None:
            chunk = self.chunks[row]
            # `terms` is already the tokenised body; only the path and the
            # signature still need splitting.
            cached = {term for term in chunk.terms if len(term) > 1}
            cached.update(
                term
                for term in tokenize(f"{chunk.path} {chunk.signature}")
                if len(term) > 1
            )
            self._chunk_term_cache[row] = cached
        return cached

    def _literal(
        self,
        query: str,
        regex: bool,
        scope: str,
    ) -> dict[int, tuple[float, int, str]]:
        if shutil.which("rg"):
            try:
                return self._literal_rg(query, regex, scope)
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
        return self._literal_python(query, regex, scope)

    def _literal_rg(
        self,
        query: str,
        regex: bool,
        scope: str,
    ) -> dict[int, tuple[float, int, str]]:
        command = [
            "rg",
            "--json",
            "--hidden",
            "--no-messages",
            "--glob", "!.git/**",
            "--glob", "!.mjj/**",
            "--glob", "!node_modules/**",
            "--glob", "!.venv/**",
        ]
        if not regex:
            command.append("-F")
        command.extend(["-e", query, scope or "."])
        process = subprocess.Popen(
            command,
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        matches: dict[int, tuple[float, int, str]] = {}
        match_count = 0
        assert process.stdout is not None
        try:
            for raw in process.stdout:
                event = json.loads(raw)
                if event.get("type") != "match":
                    continue
                data = event["data"]
                path = str(data["path"]["text"]).removeprefix("./")
                if path not in self.files:
                    continue
                line = int(data["line_number"])
                row = self.row_for_line(path, line)
                if row is None:
                    continue
                text = str(data["lines"]["text"]).rstrip("\r\n")
                occurrences = max(1, len(data.get("submatches", ())))
                old = matches.get(row)
                score = (old[0] if old else 0.0) + occurrences
                if query in text:
                    score += 0.25
                matches[row] = (
                    score,
                    old[1] if old else line,
                    old[2] if old else _short(text),
                )
                match_count += occurrences
                if match_count >= LITERAL_CANDIDATES:
                    process.kill()
                    break
        finally:
            process.stdout.close()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        return matches

    def _literal_python(
        self,
        query: str,
        regex: bool,
        scope: str,
    ) -> dict[int, tuple[float, int, str]]:
        expression = re.compile(query if regex else re.escape(query))
        matches: dict[int, tuple[float, int, str]] = {}
        match_count = 0
        for path in self.files:
            if not _in_scope(path, scope):
                continue
            try:
                lines = (self.root / path).read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
            except OSError:
                continue
            for line_number, text in enumerate(lines, 1):
                found = list(expression.finditer(text))
                if not found:
                    continue
                row = self.row_for_line(path, line_number)
                if row is None:
                    continue
                old = matches.get(row)
                score = (old[0] if old else 0.0) + len(found)
                if query in text:
                    score += 0.25
                matches[row] = (
                    score,
                    old[1] if old else line_number,
                    old[2] if old else _short(text),
                )
                match_count += len(found)
                if match_count >= LITERAL_CANDIDATES:
                    return matches
        return matches

    def format_hits(self, hits: Sequence[SearchHit]) -> str:
        if not hits:
            return "no matches"
        file_cache: dict[str, list[str]] = {}
        grouped: dict[str, list[SearchHit]] = {}
        for hit in hits:
            grouped.setdefault(hit.chunk.path, []).append(hit)

        output: list[str] = []
        for path, file_hits in grouped.items():
            grouped_file = len(file_hits) > 1
            if grouped_file:
                output.append(f"{path}:")
            for hit in file_hits:
                chunk = hit.chunk
                signature = _short(hit.context or chunk.signature)
                if grouped_file:
                    output.append(f"  {hit.line}: {signature}")
                else:
                    output.append(f"{path}:{hit.line}: {signature}")
                if hit.context:
                    continue
                lines = file_cache.get(path)
                if lines is None:
                    try:
                        lines = (self.root / path).read_text(
                            encoding="utf-8", errors="replace"
                        ).splitlines()
                    except OSError:
                        lines = []
                    file_cache[path] = lines
                for line_number in (hit.line + 1, hit.line - 1):
                    if line_number < 1 or line_number > len(lines):
                        continue
                    code = lines[line_number - 1].rstrip()
                    if not code.strip() or code.strip() == signature.strip():
                        continue
                    output.append(f"    {line_number} | {_short(code, 240)}")
                    break
        return "\n".join(output)


def build_index(
    root: str | Path,
    *,
    index_path: str | Path | None = None,
    force: bool = False,
    existing: RepositoryIndex | None = None,
) -> RepositoryIndex:
    started = time.perf_counter()
    root_path = Path(root).resolve()
    destination = (
        Path(index_path).resolve()
        if index_path is not None
        else root_path / ".mjj" / "index"
    )
    if (
        force
        or existing is not None
        and (
            existing.root != root_path
            or existing.index_path != destination
        )
    ):
        existing = None
    if existing is None and not force and destination.is_file():
        try:
            existing = RepositoryIndex.open(root_path, destination)
        except (OSError, ValueError, json.JSONDecodeError):
            existing = None

    current_files = _discover_files(root_path)
    if existing is not None and existing.files == current_files:
        existing.stats = UpdateStats(
            files=len(existing.files),
            chunks=len(existing.chunks),
            changed_files=0,
            reused_files=len(existing.files),
            elapsed_seconds=time.perf_counter() - started,
            wrote_index=False,
        )
        return existing

    old_rows: dict[str, list[int]] = {}
    if existing is not None:
        for row, chunk in enumerate(existing.chunks):
            old_rows.setdefault(chunk.path, []).append(row)

    chunks: list[Chunk] = []
    vector_data = bytearray()
    factors = array("f")
    reused_files = 0
    for relative_path, identity in current_files.items():
        if (
            existing is not None
            and existing.files.get(relative_path) == identity
            and relative_path in old_rows
        ):
            reused_files += 1
            for row in old_rows[relative_path]:
                chunks.append(existing.chunks[row])
                vector_data.extend(existing.vectors.row_bytes(row))
                factors.append(existing.vectors.factor(row))
            continue
        path = root_path / relative_path
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        text = _decode_text(raw)
        if text is None:
            continue
        for chunk, chunk_text in _make_chunks(relative_path, text):
            chunks.append(chunk)
            vector, factor = encode(
                relative_path + "\n" + chunk.signature + "\n" + chunk_text
            )
            vector_data.extend(vector)
            factors.append(factor)

    changed_files = (
        len(set(current_files) | set(existing.files if existing else ()))
        - sum(
            1
            for path, identity in current_files.items()
            if existing is not None and existing.files.get(path) == identity
        )
    )
    _write_index(
        destination,
        root_path,
        current_files,
        chunks,
        vector_data,
        factors,
    )
    result = RepositoryIndex.open(root_path, destination)
    result.stats = UpdateStats(
        files=len(current_files),
        chunks=len(chunks),
        changed_files=changed_files,
        reused_files=reused_files,
        elapsed_seconds=time.perf_counter() - started,
        wrote_index=True,
    )
    return result


def _write_index(
    destination: Path,
    root: Path,
    files: Mapping[str, tuple[int, int]],
    chunks: Sequence[Chunk],
    vector_data: bytes | bytearray,
    factors: array,
) -> None:
    if len(vector_data) != len(chunks) * DIMENSION:
        raise ValueError("chunk vectors have an invalid size")
    metadata = json.dumps(
        {
            "root": str(root),
            "files": files,
            "chunks": [chunk.to_record() for chunk in chunks],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    data_offset = (HEADER.size + len(metadata) + 63) & ~63
    padding = b"\0" * (data_offset - HEADER.size - len(metadata))
    factor_bytes = array("f", factors)
    if sys.byteorder != "little":
        factor_bytes.byteswap()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".index-",
            dir=destination.parent,
            delete=False,
        ) as output:
            temporary_name = output.name
            output.write(
                HEADER.pack(
                    MAGIC,
                    VERSION,
                    DIMENSION,
                    len(chunks),
                    len(metadata),
                    data_offset,
                )
            )
            output.write(metadata)
            output.write(padding)
            output.write(vector_data)
            output.write(factor_bytes.tobytes())
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, destination)
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def _decode_text(raw: bytes) -> str | None:
    if b"\0" in raw[:8192]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def _make_chunks(path: str, text: str) -> Iterator[tuple[Chunk, str]]:
    lines = text.splitlines()
    if not lines:
        return
    starts = [0]
    starts.extend(
        line_number
        for line_number, line in enumerate(lines)
        if line_number and _DECLARATION.match(line)
    )
    starts = sorted(set(starts))
    boundaries: list[int] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        while start < end:
            boundaries.append(start)
            start = min(start + MAX_CHUNK_LINES, end)
    boundaries = sorted(set(boundaries))
    for position, start in enumerate(boundaries):
        end = (
            boundaries[position + 1]
            if position + 1 < len(boundaries)
            else len(lines)
        )
        body_lines = lines[start:end]
        if not any(line.strip() for line in body_lines):
            continue
        signature = next(
            (_short(line.strip()) for line in body_lines if line.strip()),
            Path(path).name,
        )
        body = "\n".join(body_lines)
        yield (
            Chunk(
                path=path,
                start_line=start + 1,
                end_line=end,
                signature=signature,
                terms=term_frequencies(
                    body, path=path, signature=signature
                ),
            ),
            body,
        )


def _discover_files(root: Path) -> dict[str, tuple[int, int]]:
    paths = _git_files(root)
    if paths is None:
        paths = _walk_files(root)
    result: dict[str, tuple[int, int]] = {}
    for relative in sorted(set(paths)):
        relative = relative.replace(os.sep, "/").removeprefix("./")
        if not relative or _hard_skipped(relative):
            continue
        path = root / relative
        try:
            stat = path.stat()
        except OSError:
            continue
        if (
            not path.is_file()
            or path.is_symlink()
            or stat.st_size > MAX_FILE_BYTES
            or path.suffix.lower() in _BINARY_SUFFIXES
        ):
            continue
        result[relative] = (stat.st_mtime_ns, stat.st_size)
    return result


def _fallback_files(
    root: Path,
    indexed: Mapping[str, tuple[int, int]],
    scope: str,
) -> Iterator[tuple[str, int]]:
    """Yield excluded candidate files without entering dependency/build trees."""
    scope = _normalise_scope(scope)
    target = root / scope if scope else root
    if target.is_file():
        candidates: Iterable[Path] = (target,)
    elif target.is_dir():
        def walked() -> Iterator[Path]:
            seen = 0
            for directory, names, filenames in os.walk(target, topdown=True):
                relative_directory = Path(directory).relative_to(root)
                names[:] = [
                    name
                    for name in names
                    if not _hard_skipped((relative_directory / name).as_posix())
                ]
                for name in filenames:
                    if seen >= MAX_FALLBACK_FILES:
                        return
                    seen += 1
                    yield Path(directory) / name

        candidates = walked()
    else:
        return
    for path in candidates:
        try:
            relative = path.relative_to(root).as_posix()
            stat_result = path.stat()
        except (OSError, ValueError):
            continue
        if (
            relative in indexed
            or not path.is_file()
            or path.is_symlink()
            or stat_result.st_size > MAX_FALLBACK_FILE_BYTES
            or path.suffix.lower() in _BINARY_SUFFIXES
        ):
            continue
        yield relative, stat_result.st_size


def _git_files(root: Path) -> list[str] | None:
    if not shutil.which("git"):
        return None
    try:
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
        if probe.returncode:
            return None
        listing = subprocess.run(
            [
                "git", "-C", str(root), "ls-files",
                "-co", "--exclude-standard", "-z", "--", ".",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        if listing.returncode:
            return None
        return [
            value.decode("utf-8", errors="surrogateescape")
            for value in listing.stdout.split(b"\0")
            if value
        ]
    except (OSError, subprocess.SubprocessError):
        return None


@dataclass
class _IgnoreRule:
    base: str
    pattern: str
    negative: bool
    directory_only: bool


def _walk_files(root: Path) -> list[str]:
    output: list[str] = []
    rules: list[_IgnoreRule] = []
    for directory, names, filenames in os.walk(root, topdown=True):
        directory_path = Path(directory)
        relative_directory = directory_path.relative_to(root).as_posix()
        if relative_directory == ".":
            relative_directory = ""
        ignore_file = directory_path / ".gitignore"
        if ignore_file.is_file():
            try:
                content = ignore_file.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                content = ""
            for line in content.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                negative = stripped.startswith("!")
                if negative:
                    stripped = stripped[1:]
                directory_only = stripped.endswith("/")
                stripped = stripped.rstrip("/")
                if stripped:
                    rules.append(
                        _IgnoreRule(
                            relative_directory,
                            stripped,
                            negative,
                            directory_only,
                        )
                    )
        kept_names = []
        for name in names:
            relative = "/".join(
                part for part in (relative_directory, name) if part
            )
            if _hard_skipped(relative) or _ignored(relative, True, rules):
                continue
            kept_names.append(name)
        names[:] = kept_names
        for name in filenames:
            relative = "/".join(
                part for part in (relative_directory, name) if part
            )
            if not _hard_skipped(relative) and not _ignored(
                relative, False, rules
            ):
                output.append(relative)
    return output


def _ignored(path: str, is_directory: bool, rules: Iterable[_IgnoreRule]) -> bool:
    ignored = False
    for rule in rules:
        if rule.directory_only and not is_directory:
            continue
        relative = path
        if rule.base:
            prefix = rule.base + "/"
            if not path.startswith(prefix):
                continue
            relative = path[len(prefix):]
        pattern = rule.pattern.lstrip("/")
        if "/" in pattern:
            matched = fnmatch.fnmatchcase(relative, pattern)
        else:
            matched = any(
                fnmatch.fnmatchcase(part, pattern)
                for part in relative.split("/")
            )
        if matched:
            ignored = not rule.negative
    return ignored


def _hard_skipped(path: str) -> bool:
    return any(part.lower() in _HARD_SKIP_DIRS for part in Path(path).parts)


def _normalise_scope(scope: str) -> str:
    value = scope.replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    value = value.strip("/")
    if value in {"", "."}:
        return ""
    if value == ".." or value.startswith("../") or "/../" in value:
        raise ValueError("search path must stay inside the indexed root")
    return value


def _in_scope(path: str, scope: str) -> bool:
    return not scope or path == scope or path.startswith(scope + "/")


def _adaptive_count(hits: Sequence[SearchHit]) -> int:
    """Stop at an early confidence cliff, otherwise spend the caller's limit."""
    if len(hits) < 2:
        return len(hits)
    inspected = min(SCORE_CLIFF_WINDOW, len(hits) - 1)
    for position in range(inspected):
        score = hits[position].score
        following = hits[position + 1].score
        if (
            score >= CONFIDENT_SCORE
            and following <= score * SCORE_CLIFF_RATIO
        ):
            return position + 1
    return len(hits)


def _short(value: str, limit: int = 200) -> str:
    value = value.replace("\t", "    ").strip()
    return value if len(value) <= limit else value[:limit - 1] + "…"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="build an MJJ repository index")
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--index")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    index = build_index(args.root, index_path=args.index, force=args.force)
    action = "wrote" if index.stats.wrote_index else "reused"
    print(
        f"{action} {index.index_path}: {index.stats.files} files, "
        f"{index.stats.chunks} chunks in "
        f"{index.stats.elapsed_seconds * 1000:.1f} ms "
        f"({index.backend_name})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
