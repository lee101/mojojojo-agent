"""Deterministic int8 code vectors and the optional mojo-embed scan backend."""

from __future__ import annotations

import ctypes
import ctypes.util
import math
import os
import threading
import zlib
from array import array
from collections.abc import Iterable, Sequence
from pathlib import Path

from mjj.kernels import quantize_i8

from .lexical import tokenize

DIMENSION = 256
_HASH_HIGH_SEED = 0x9E3779B9
_TOKEN_PREFIX = (zlib.crc32(b"token:"), zlib.crc32(b"token:", _HASH_HIGH_SEED))
_NGRAM_PREFIX = (zlib.crc32(b"ngram:"), zlib.crc32(b"ngram:", _HASH_HIGH_SEED))
_START_MARKER = ord("^")
_END_MARKER = ord("$")
_HASH_SIGN_BIT = 1 << 63


def _hash64(value: str) -> int:
    # crc32 is stable across processes and performs the byte loop in C. Two
    # independently seeded passes provide enough bits for a bucket and sign.
    encoded = value.encode("utf-8", "surrogatepass")
    low = zlib.crc32(encoded)
    high = zlib.crc32(encoded, _HASH_HIGH_SEED)
    return low | (high << 32)


def _project_hashed(values: list[float], low: int, high: int, weight: float) -> None:
    hashed = low | (high << 32)
    position = hashed % len(values)
    values[position] += weight if hashed & _HASH_SIGN_BIT else -weight


def static_embedding(text: str, dim: int = DIMENSION) -> list[float]:
    """Hash identifier tokens and their character n-grams into a unit vector."""
    return static_embedding_tokens(tokenize(text), dim)


def _l2_normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    if not norm:
        return values
    inverse = 1.0 / norm
    return [value * inverse for value in values]


def static_embedding_tokens_python(
    tokens: Iterable[str],
    dim: int = DIMENSION,
) -> list[float]:
    """Pure-Python static embedding used as the portable fallback."""
    if dim <= 0:
        raise ValueError("vector dimension must be positive")
    values = [0.0] * dim
    frequencies: dict[str, int] = {}
    for token in tokens:
        frequencies[token] = frequencies.get(token, 0) + 1
    crc32 = zlib.crc32
    project = _project_hashed
    token_prefix_low, token_prefix_high = _TOKEN_PREFIX
    ngram_prefix_low, ngram_prefix_high = _NGRAM_PREFIX
    for token, frequency in frequencies.items():
        token_weight = 1.5 * math.sqrt(frequency)
        encoded = token.encode("utf-8", "surrogatepass")
        low = crc32(encoded, token_prefix_low)
        high = crc32(encoded, token_prefix_high)
        project(values, low, high, token_weight)
        token_length = len(encoded)
        marked = bytearray(token_length + 2)
        marked[0] = _START_MARKER
        marked[1:-1] = encoded
        marked[-1] = _END_MARKER
        marked_bytes = memoryview(marked)
        for width, weight in ((3, 0.45), (4, 0.65)):
            if token_length + 2 < width:
                continue
            for start in range(token_length + 3 - width):
                span = marked_bytes[start:start + width]
                low = crc32(span, ngram_prefix_low)
                high = crc32(span, ngram_prefix_high)
                span.release()
                project(values, low, high, weight)
        marked_bytes.release()
    return _l2_normalize(values)


def static_embedding_tokens(
    tokens: Iterable[str],
    dim: int = DIMENSION,
) -> list[float]:
    """Embed an existing ordered token stream without tokenizing it again."""
    if dim <= 0:
        raise ValueError("vector dimension must be positive")
    materialised = list(tokens)
    native = native_static_embedding_tokens(materialised, dim)
    if native is not None:
        return native
    return static_embedding_tokens_python(materialised, dim)


def static_embedding_tokens_batch(
    bags: Sequence[Iterable[str]],
    dim: int = DIMENSION,
) -> list[list[float]]:
    """Embed many token bags; uses one Mojo call when the batch ABI is present."""
    if dim <= 0:
        raise ValueError("vector dimension must be positive")
    materialised = [list(bag) for bag in bags]
    native = native_static_embedding_tokens_batch(materialised, dim)
    if native is not None:
        return native
    return [
        static_embedding_tokens_python(bag, dim) for bag in materialised
    ]


def quantize(values: Sequence[float]) -> tuple[bytes, float]:
    """Symmetrically quantise a vector; return bytes and scale/norm factor."""
    numeric = array("d", (float(value) for value in values))
    # Keep CPython's float summation here.  Python 3.12+ uses compensated
    # summation, whose final bit cannot be reproduced by Mojo's scalar/SIMD
    # reductions; the persistent factor must remain exactly compatible.
    norm = math.sqrt(sum(float(value) * float(value) for value in numeric))
    output = array("q", [0]) * len(numeric)
    scale = quantize_i8(numeric, output)
    return (
        array("b", output).tobytes(),
        scale / norm if norm else 1.0,
    )


def encode(text: str, dim: int = DIMENSION) -> tuple[bytes, float]:
    return quantize(static_embedding(text, dim))


def encode_tokens(
    tokens: Iterable[str],
    dim: int = DIMENSION,
) -> tuple[bytes, float]:
    return quantize(static_embedding_tokens(tokens, dim))


def _library_candidates() -> list[str]:
    candidates: list[str] = []
    configured = os.environ.get("MJJ_MOJO_EMBED_LIB")
    if configured:
        candidates.append(configured)
    repository = Path(__file__).resolve().parents[2]
    development_build = repository.parent / "mojo-embed" / "build"
    local_build = repository / "build"
    package = Path(__file__).parent
    library_names = (
        "mjj_search.dll",
        "libmjj_search.so",
        "libmjj_search.dylib",
        "mojo_embed.dll",
        "libmojo_embed.so",
        "libmojo_embed.dylib",
    )
    for directory in (local_build, development_build, package):
        candidates.extend(str(directory / name) for name in library_names)
    for name in ("mjj_search", "mojo_embed"):
        discovered = ctypes.util.find_library(name)
        if discovered:
            candidates.append(discovered)
    return candidates


class MojoBackend:
    """Guarded ctypes binding to Mojo search/tokenize/embed/BM25/quantize exports."""

    def __init__(self) -> None:
        self.library: ctypes.CDLL | None = None
        self.path = ""
        self.error = ""
        self.search_function = None
        self.tokenize_function = None
        self.embed_function = None
        self.embed_batch_function = None
        self.bm25_function = None
        self.quantize_function = None
        integer = ctypes.c_ssize_t
        best: tuple[
            int,
            object,
            object,
            object,
            object,
            object,
            object,
            object,
            str,
        ] | None = None
        for candidate in _library_candidates():
            try:
                library = ctypes.CDLL(candidate)
                search = getattr(library, "mjj_search_i8_mmap", None)
                if search is None:
                    search = getattr(library, "embed_search_i8", None)
                if search is None:
                    raise AttributeError("missing search export")
                search.argtypes = [
                    integer,
                    integer,
                    integer,
                    integer,
                    integer,
                    integer,
                    integer,
                    integer,
                    integer,
                    integer,
                    ctypes.c_float,
                ]
                search.restype = None
                tokenize = getattr(library, "mjj_tokenize", None)
                embed = getattr(library, "mjj_static_embed", None)
                embed_batch = getattr(library, "mjj_static_embed_batch", None)
                bm25 = getattr(library, "mjj_bm25_accumulate", None)
                quantize = getattr(library, "mjj_quantize_i8", None)
                if tokenize is not None:
                    tokenize.argtypes = [integer] * 9
                    tokenize.restype = ctypes.c_int
                if embed is not None:
                    embed.argtypes = [integer] * 7
                    embed.restype = ctypes.c_int
                if embed_batch is not None:
                    embed_batch.argtypes = [integer] * 8
                    embed_batch.restype = ctypes.c_int
                if bm25 is not None:
                    bm25.argtypes = [
                        integer,
                        integer,
                        integer,
                        integer,
                        integer,
                        ctypes.c_double,
                        ctypes.c_double,
                        ctypes.c_double,
                        ctypes.c_double,
                        ctypes.c_double,
                    ]
                    bm25.restype = ctypes.c_int
                if quantize is not None:
                    quantize.argtypes = [integer, integer, integer, integer]
                    quantize.restype = ctypes.c_int
                score = 1 + sum(
                    function is not None
                    for function in (
                        tokenize,
                        embed,
                        embed_batch,
                        bm25,
                        quantize,
                    )
                )
                if best is None or score > best[0]:
                    best = (
                        score,
                        library,
                        search,
                        tokenize,
                        embed,
                        embed_batch,
                        bm25,
                        quantize,
                        candidate,
                    )
                if score >= 6:
                    break
            except (AttributeError, OSError) as exc:
                self.error = str(exc)
        if best is not None:
            (
                _,
                self.library,
                self.search_function,
                self.tokenize_function,
                self.embed_function,
                self.embed_batch_function,
                self.bm25_function,
                self.quantize_function,
                self.path,
            ) = best

    @property
    def available(self) -> bool:
        return self.library is not None

    @property
    def tokenize_available(self) -> bool:
        return self.tokenize_function is not None and _accel_requested()

    @property
    def embed_available(self) -> bool:
        return self.embed_function is not None and _accel_requested()

    @property
    def embed_batch_available(self) -> bool:
        return self.embed_batch_function is not None and _accel_requested()

    @property
    def bm25_available(self) -> bool:
        return self.bm25_function is not None and _accel_requested()

    @property
    def quantize_available(self) -> bool:
        return self.quantize_function is not None and _accel_requested()


def _accel_requested() -> bool:
    return os.environ.get("MJJ_ACCEL", "1") != "0"


_BACKEND: MojoBackend | None = None
_BACKEND_LOCK = threading.Lock()


def _default_backend() -> MojoBackend:
    """Load the optional shared library only when a vector matrix needs it."""
    global _BACKEND
    if _BACKEND is None:
        with _BACKEND_LOCK:
            if _BACKEND is None:
                _BACKEND = MojoBackend()
    return _BACKEND


def _address(buffer, c_type) -> tuple[int, object]:
    view = memoryview(buffer)
    holder = (c_type * (view.nbytes // ctypes.sizeof(c_type))).from_buffer(view)
    return ctypes.addressof(holder), holder


def native_tokenize(value: str) -> list[str] | None:
    """Return Mojo tokens when the ABI is loaded; otherwise ``None``."""
    backend = _default_backend()
    if not backend.tokenize_available:
        return None
    encoded = value.encode("utf-8", "surrogatepass")
    if not encoded:
        return []
    text = bytearray(encoded)
    out_cap = max(64, len(text) * 3)
    max_tokens = max(16, len(text) + 1)
    for _ in range(3):
        out = bytearray(out_cap)
        offsets = array("i", [0]) * max_tokens
        lengths = array("i", [0]) * max_tokens
        token_count = ctypes.c_ssize_t(0)
        out_len = ctypes.c_ssize_t(0)
        text_address, text_holder = _address(text, ctypes.c_uint8)
        out_address, out_holder = _address(out, ctypes.c_uint8)
        offsets_address, offsets_holder = _address(offsets, ctypes.c_int32)
        lengths_address, lengths_holder = _address(lengths, ctypes.c_int32)
        _ = (text_holder, out_holder, offsets_holder, lengths_holder)
        status = backend.tokenize_function(
            text_address,
            len(text),
            out_address,
            len(out),
            offsets_address,
            lengths_address,
            max_tokens,
            ctypes.addressof(token_count),
            ctypes.addressof(out_len),
        )
        if status == 0:
            tokens: list[str] = []
            for index in range(int(token_count.value)):
                start = int(offsets[index])
                length = int(lengths[index])
                tokens.append(bytes(out[start:start + length]).decode("ascii"))
            return tokens
        out_cap = max(out_cap * 2, int(out_len.value) + len(text) + 64)
        max_tokens = max(max_tokens * 2, int(token_count.value) + 64)
    return None


def _pack_token_bag(
    tokens: Iterable[str],
) -> tuple[list[bytes], array, array, array]:
    frequencies: dict[str, int] = {}
    for token in tokens:
        frequencies[token] = frequencies.get(token, 0) + 1
    parts: list[bytes] = []
    offsets = array("i")
    lengths = array("i")
    freqs = array("i")
    cursor = 0
    for token, frequency in frequencies.items():
        encoded = token.encode("utf-8", "surrogatepass")
        offsets.append(cursor)
        lengths.append(len(encoded))
        freqs.append(int(frequency))
        parts.append(encoded)
        cursor += len(encoded)
    return parts, offsets, lengths, freqs


def native_static_embedding_tokens(
    tokens: Iterable[str],
    dim: int = DIMENSION,
) -> list[float] | None:
    """Mojo projection + CPython L2; ``None`` when the ABI is unavailable."""
    backend = _default_backend()
    if not backend.embed_available or dim <= 0:
        return None
    parts, offsets, lengths, freqs = _pack_token_bag(tokens)
    if not offsets:
        return [0.0] * dim
    blob = bytearray(b"".join(parts))
    values = array("d", [0.0]) * dim
    blob_address, blob_holder = _address(blob, ctypes.c_uint8)
    offsets_address, offsets_holder = _address(offsets, ctypes.c_int32)
    lengths_address, lengths_holder = _address(lengths, ctypes.c_int32)
    freqs_address, freqs_holder = _address(freqs, ctypes.c_int32)
    values_address, values_holder = _address(values, ctypes.c_double)
    _ = (blob_holder, offsets_holder, lengths_holder, freqs_holder, values_holder)
    status = backend.embed_function(
        blob_address,
        offsets_address,
        lengths_address,
        freqs_address,
        len(offsets),
        values_address,
        dim,
    )
    if status != 0:
        return None
    return _l2_normalize(list(values))


def native_static_embedding_tokens_batch(
    bags: Sequence[Iterable[str]],
    dim: int = DIMENSION,
) -> list[list[float]] | None:
    """One Mojo batch projection + per-row CPython L2; ``None`` if unavailable.

    Falls back to repeated single-bag native embeds when only ``mjj_static_embed``
    is present, so callers still avoid the Python projection loop.
    """
    backend = _default_backend()
    if dim <= 0:
        return None
    materialised = [list(bag) for bag in bags]
    bag_count = len(materialised)
    if bag_count == 0:
        return []
    if not backend.embed_batch_available:
        if not backend.embed_available:
            return None
        results: list[list[float]] = []
        for bag in materialised:
            embedded = native_static_embedding_tokens(bag, dim)
            if embedded is None:
                return None
            results.append(embedded)
        return results

    blob = bytearray()
    offsets = array("i")
    lengths = array("i")
    freqs = array("i")
    bag_offsets = array("i", [0])
    for bag in materialised:
        frequencies: dict[str, int] = {}
        for token in bag:
            frequencies[token] = frequencies.get(token, 0) + 1
        for token, frequency in frequencies.items():
            encoded = token.encode("utf-8", "surrogatepass")
            offsets.append(len(blob))
            lengths.append(len(encoded))
            freqs.append(frequency)
            blob.extend(encoded)
        bag_offsets.append(len(offsets))

    values = array("d", [0.0]) * (bag_count * dim)
    if not offsets:
        return [[0.0] * dim for _ in range(bag_count)]
    blob_address, blob_holder = _address(blob, ctypes.c_uint8)
    offsets_address, offsets_holder = _address(offsets, ctypes.c_int32)
    lengths_address, lengths_holder = _address(lengths, ctypes.c_int32)
    freqs_address, freqs_holder = _address(freqs, ctypes.c_int32)
    bag_offsets_address, bag_offsets_holder = _address(
        bag_offsets, ctypes.c_int32
    )
    values_address, values_holder = _address(values, ctypes.c_double)
    _ = (
        blob_holder,
        offsets_holder,
        lengths_holder,
        freqs_holder,
        bag_offsets_holder,
        values_holder,
    )
    status = backend.embed_batch_function(
        blob_address,
        offsets_address,
        lengths_address,
        freqs_address,
        bag_offsets_address,
        bag_count,
        values_address,
        dim,
    )
    if status != 0:
        return None
    flat = list(values)
    return [
        _l2_normalize(flat[index * dim:(index + 1) * dim])
        for index in range(bag_count)
    ]


def native_bm25_accumulate(
    document_ids: Sequence[int],
    frequencies: Sequence[int],
    lengths: Sequence[float],
    scores: Sequence[float],
    average_length: float,
    k1: float,
    b: float,
    inverse_frequency: float,
    query_boost: float,
) -> int | None:
    """Mojo BM25 posting accumulate; ``None`` when the ABI is unavailable."""
    backend = _default_backend()
    if not backend.bm25_available:
        return None
    posting_len = len(document_ids)
    if posting_len != len(frequencies) or average_length == 0.0:
        return None
    doc_ids = (
        document_ids
        if isinstance(document_ids, array) and document_ids.typecode == "q"
        else array("q", (int(value) for value in document_ids))
    )
    freqs = (
        frequencies
        if isinstance(frequencies, array) and frequencies.typecode == "q"
        else array("q", (int(value) for value in frequencies))
    )
    length_buf = (
        lengths
        if isinstance(lengths, array) and lengths.typecode == "d"
        else array("d", (float(value) for value in lengths))
    )
    score_buf = (
        scores
        if isinstance(scores, array) and scores.typecode == "d"
        else array("d", (float(value) for value in scores))
    )
    if posting_len == 0:
        return 0
    doc_address, doc_holder = _address(doc_ids, ctypes.c_int64)
    freq_address, freq_holder = _address(freqs, ctypes.c_int64)
    length_address, length_holder = _address(length_buf, ctypes.c_double)
    score_address, score_holder = _address(score_buf, ctypes.c_double)
    _ = (doc_holder, freq_holder, length_holder, score_holder)
    status = backend.bm25_function(
        doc_address,
        freq_address,
        length_address,
        score_address,
        posting_len,
        float(average_length),
        float(k1),
        float(b),
        float(inverse_frequency),
        float(query_boost),
    )
    if status < 0:
        return None
    if score_buf is not scores:
        for index, value in enumerate(score_buf):
            scores[index] = float(value)
    return int(status)


def native_quantize_i8(
    values: Sequence[float],
    output: Sequence[int],
) -> float | None:
    """SIMD Mojo quantise; ``None`` when the ABI is unavailable."""
    backend = _default_backend()
    if not backend.quantize_available:
        return None
    count = len(values)
    if count != len(output):
        return None
    numeric = (
        values
        if isinstance(values, array) and values.typecode == "d"
        else array("d", (float(value) for value in values))
    )
    out = (
        output
        if isinstance(output, array) and output.typecode == "q"
        else array("q", [0]) * count
    )
    if count == 0:
        return 1.0
    scale = ctypes.c_double(0.0)
    values_address, values_holder = _address(numeric, ctypes.c_double)
    output_address, output_holder = _address(out, ctypes.c_int64)
    _ = (values_holder, output_holder)
    status = backend.quantize_function(
        values_address,
        output_address,
        count,
        ctypes.addressof(scale),
    )
    if status != 0:
        return None
    if out is not output:
        for index, value in enumerate(out):
            output[index] = int(value)
    return float(scale.value)


class Int8Vectors:
    """A contiguous vector matrix, optionally backed directly by an mmap."""

    def __init__(
        self,
        data,
        factors,
        *,
        dim: int = DIMENSION,
        backend: MojoBackend | None = None,
    ) -> None:
        self.dim = dim
        self.data = data if isinstance(data, memoryview) else bytearray(data)
        self.factors = (
            factors
            if isinstance(factors, memoryview)
            else array("f", (float(value) for value in factors))
        )
        data_size = memoryview(self.data).nbytes
        if dim <= 0 or data_size % dim:
            raise ValueError("int8 matrix size is not divisible by its dimension")
        self.count = data_size // dim
        if len(self.factors) != self.count:
            raise ValueError("one vector factor is required per row")
        self.backend = _default_backend() if backend is None else backend
        self._ids = array("q", range(self.count))
        self._scratch = array("f", [0.0]) * self.count
        self._lock = threading.Lock()

    @property
    def backend_name(self) -> str:
        return "mojo-embed" if self.backend.available else "python"

    def row_bytes(self, row: int) -> bytes:
        start = row * self.dim
        return bytes(memoryview(self.data)[start:start + self.dim])

    def factor(self, row: int) -> float:
        return float(self.factors[row])

    def search_text(self, query: str, k: int = 20) -> list[tuple[int, float]]:
        query_data, query_factor = encode(query, self.dim)
        return self.search(query_data, query_factor, k)

    def search(
        self,
        query_data: bytes,
        query_factor: float,
        k: int,
    ) -> list[tuple[int, float]]:
        wanted = min(max(0, int(k)), self.count)
        if not wanted:
            return []
        if len(query_data) != self.dim:
            raise ValueError("query vector has the wrong dimension")
        if self.backend.available:
            try:
                return self._native_search(query_data, query_factor, wanted)
            except (BufferError, TypeError, ValueError):
                # A read-only or oddly aligned buffer should only cost speed.
                pass
        return self._python_search(query_data, query_factor, wanted)

    def _native_search(
        self,
        query_data: bytes,
        query_factor: float,
        wanted: int,
    ) -> list[tuple[int, float]]:
        query = bytearray(query_data)
        output_ids = array("q", [-1]) * wanted
        output_scores = array("f", [0.0]) * wanted
        with self._lock:
            data_address, data_holder = _address(self.data, ctypes.c_int8)
            factors_address, factors_holder = _address(
                self.factors, ctypes.c_float
            )
            ids_address, ids_holder = _address(self._ids, ctypes.c_int64)
            query_address, query_holder = _address(query, ctypes.c_int8)
            scratch_address, scratch_holder = _address(
                self._scratch, ctypes.c_float
            )
            output_ids_address, output_ids_holder = _address(
                output_ids, ctypes.c_int64
            )
            output_scores_address, output_scores_holder = _address(
                output_scores, ctypes.c_float
            )
            holders = (
                data_holder,
                factors_holder,
                ids_holder,
                query_holder,
                scratch_holder,
                output_ids_holder,
                output_scores_holder,
            )
            _ = holders
            self.backend.search_function(
                data_address,
                factors_address,
                ids_address,
                query_address,
                scratch_address,
                output_ids_address,
                output_scores_address,
                self.count,
                self.dim,
                wanted,
                ctypes.c_float(query_factor),
            )
        return [
            (int(document_id), float(score))
            for document_id, score in zip(output_ids, output_scores)
            if document_id >= 0
        ]

    def _python_search(
        self,
        query_data: bytes,
        query_factor: float,
        wanted: int,
    ) -> list[tuple[int, float]]:
        query = memoryview(query_data).cast("b")
        data = memoryview(self.data).cast("b")
        scored: list[tuple[int, float]] = []
        for row in range(self.count):
            start = row * self.dim
            dot = 0
            for offset in range(self.dim):
                dot += query[offset] * data[start + offset]
            scored.append(
                (row, dot * query_factor * float(self.factors[row]))
            )
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:wanted]
