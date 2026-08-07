"""Direct ABI smoke test used by the Mojo CI environment."""

from __future__ import annotations

import ctypes
import math
import sys
from pathlib import Path


def _check_search(library: ctypes.CDLL) -> None:
    search = library.mjj_search_i8_mmap
    integer = ctypes.c_ssize_t
    search.argtypes = [integer] * 10 + [ctypes.c_float]
    search.restype = None

    data = (ctypes.c_int8 * 6)(3, 2, -1, -3, 5, 5)
    factors = (ctypes.c_float * 3)(1.0, 1.0, 1.0)
    identifiers = (ctypes.c_int64 * 3)(10, 20, 30)
    query = (ctypes.c_int8 * 2)(1, 1)
    scores = (ctypes.c_float * 3)()
    output_ids = (ctypes.c_int64 * 2)()
    output_scores = (ctypes.c_float * 2)()

    search(
        ctypes.addressof(data),
        ctypes.addressof(factors),
        ctypes.addressof(identifiers),
        ctypes.addressof(query),
        ctypes.addressof(scores),
        ctypes.addressof(output_ids),
        ctypes.addressof(output_scores),
        3,
        2,
        2,
        1.0,
    )

    assert list(output_ids) == [30, 10], list(output_ids)
    assert list(output_scores) == [10.0, 5.0], list(output_scores)


def _check_tokenize(library: ctypes.CDLL) -> None:
    tokenize = library.mjj_tokenize
    integer = ctypes.c_ssize_t
    tokenize.argtypes = [integer] * 9
    tokenize.restype = ctypes.c_int

    text = b"HTTPServer.fetch_user"
    text_buf = (ctypes.c_uint8 * len(text)).from_buffer_copy(text)
    out = (ctypes.c_uint8 * 64)()
    offsets = (ctypes.c_int32 * 16)()
    lengths = (ctypes.c_int32 * 16)()
    token_count = ctypes.c_ssize_t(0)
    out_len = ctypes.c_ssize_t(0)
    status = tokenize(
        ctypes.addressof(text_buf),
        len(text),
        ctypes.addressof(out),
        len(out),
        ctypes.addressof(offsets),
        ctypes.addressof(lengths),
        len(offsets),
        ctypes.addressof(token_count),
        ctypes.addressof(out_len),
    )
    assert status == 0, status
    tokens = []
    for index in range(token_count.value):
        start = offsets[index]
        length = lengths[index]
        tokens.append(bytes(out[start:start + length]).decode("ascii"))
    assert tokens == ["httpserver", "http", "server", "fetch", "user"], tokens


def _check_embed(library: ctypes.CDLL) -> None:
    embed = library.mjj_static_embed
    integer = ctypes.c_ssize_t
    embed.argtypes = [integer] * 7
    embed.restype = ctypes.c_int

    token = b"http"
    offsets = (ctypes.c_int32 * 1)(0)
    lengths = (ctypes.c_int32 * 1)(len(token))
    freqs = (ctypes.c_int32 * 1)(1)
    values = (ctypes.c_double * 8)()
    token_buf = (ctypes.c_uint8 * len(token)).from_buffer_copy(token)
    status = embed(
        ctypes.addressof(token_buf),
        ctypes.addressof(offsets),
        ctypes.addressof(lengths),
        ctypes.addressof(freqs),
        1,
        ctypes.addressof(values),
        8,
    )
    assert status == 0, status
    assert any(value != 0.0 for value in values), list(values)
    norm = math.sqrt(sum(value * value for value in values))
    assert norm > 0.0


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: native_search_smoke.py LIBRARY")
    library = ctypes.CDLL(str(Path(sys.argv[1]).resolve()))
    _check_search(library)
    _check_tokenize(library)
    _check_embed(library)
    print("native search ABI ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
