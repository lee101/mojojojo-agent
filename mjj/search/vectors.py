"""Deterministic int8 code vectors and the optional mojo-embed scan backend."""

from __future__ import annotations

import ctypes
import ctypes.util
import math
import os
import threading
import zlib
from array import array
from pathlib import Path
from typing import Sequence

from .lexical import tokenize


DIMENSION = 256
def _hash64(value: str) -> int:
    # crc32 is stable across processes and performs the byte loop in C. Two
    # independently seeded passes provide enough bits for a bucket and sign.
    encoded = value.encode("utf-8", "surrogatepass")
    low = zlib.crc32(encoded)
    high = zlib.crc32(encoded, 0x9E3779B9)
    return low | (high << 32)


def _project(values: list[float], feature: str, weight: float) -> None:
    hashed = _hash64(feature)
    position = hashed % len(values)
    values[position] += weight if hashed & (1 << 63) else -weight


def static_embedding(text: str, dim: int = DIMENSION) -> list[float]:
    """Hash identifier tokens and their character n-grams into a unit vector."""
    if dim <= 0:
        raise ValueError("vector dimension must be positive")
    values = [0.0] * dim
    frequencies: dict[str, int] = {}
    for token in tokenize(text):
        frequencies[token] = frequencies.get(token, 0) + 1
    for token, frequency in frequencies.items():
        token_weight = 1.5 * math.sqrt(frequency)
        _project(values, "token:" + token, token_weight)
        marked = "^" + token + "$"
        for width, weight in ((3, 0.45), (4, 0.65)):
            if len(marked) < width:
                continue
            for offset in range(len(marked) - width + 1):
                _project(
                    values,
                    f"ngram:{marked[offset:offset + width]}",
                    weight,
                )
    norm = math.sqrt(sum(value * value for value in values))
    if norm:
        inverse = 1.0 / norm
        values = [value * inverse for value in values]
    return values


def quantize(values: Sequence[float]) -> tuple[bytes, float]:
    """Symmetrically quantise a vector; return bytes and scale/norm factor."""
    norm = math.sqrt(sum(float(value) * float(value) for value in values))
    peak = max((abs(float(value)) for value in values), default=0.0)
    scale = peak / 127.0 if peak else 1.0
    inverse = 1.0 / scale
    output = array(
        "b",
        (
            max(-127, min(127, int(value * inverse + (0.5 if value >= 0 else -0.5))))
            for value in values
        ),
    )
    return output.tobytes(), scale / norm if norm else 1.0


def encode(text: str, dim: int = DIMENSION) -> tuple[bytes, float]:
    return quantize(static_embedding(text, dim))


def _library_candidates() -> list[str]:
    candidates: list[str] = []
    configured = os.environ.get("MJJ_MOJO_EMBED_LIB")
    if configured:
        candidates.append(configured)
    repository = Path(__file__).resolve().parents[2]
    candidates.append(
        str(repository.parent / "mojo-embed" / "build" / "libmojo_embed.so")
    )
    candidates.append(str(Path(__file__).with_name("libmojo_embed.so")))
    discovered = ctypes.util.find_library("mojo_embed")
    if discovered:
        candidates.append(discovered)
    return candidates


class MojoBackend:
    """Guarded ctypes binding to mojo-embed's zero-copy top-k function."""

    def __init__(self) -> None:
        self.library: ctypes.CDLL | None = None
        self.path = ""
        self.error = ""
        for candidate in _library_candidates():
            try:
                library = ctypes.CDLL(candidate)
                search = getattr(library, "mjj_search_i8_mmap", None)
                if search is None:
                    search = getattr(library, "embed_search_i8")
                integer = ctypes.c_ssize_t
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
                self.library = library
                self.search_function = search
                self.path = candidate
                break
            except (AttributeError, OSError) as exc:
                self.error = str(exc)

    @property
    def available(self) -> bool:
        return self.library is not None


BACKEND = MojoBackend()


def _address(buffer, c_type) -> tuple[int, object]:
    view = memoryview(buffer)
    holder = (c_type * (view.nbytes // ctypes.sizeof(c_type))).from_buffer(view)
    return ctypes.addressof(holder), holder


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
        self.backend = BACKEND if backend is None else backend
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
