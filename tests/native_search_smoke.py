"""Direct ABI smoke test used by the Mojo CI environment."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: native_search_smoke.py LIBRARY")
    library = ctypes.CDLL(str(Path(sys.argv[1]).resolve()))
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
    print("native search ABI ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
