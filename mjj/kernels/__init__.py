"""Optional mojosub / Mojo-ABI acceleration for measured hot loops.

The functions exported here always work. Preference for BM25:

1. Repository ``libmjj_search.so`` C ABI (no JIT wait; ~50x on a 5k posting).
2. mojosub tiered JIT at ``opt=0`` when the shared library is absent.
3. Pure Python.

Default ``mojo build`` may differ from CPython by one ulp; ranking is unchanged.

``MJJ_ACCEL=0`` forces the pure-Python path for every kernel.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from .text import bm25_accumulate_python, quantize_i8_python

__all__ = [
    "ACCEL_ENABLED",
    "ACCEL_REASON",
    "bm25_accumulate",
    "bm25_accumulate_python",
    "quantize_i8",
    "quantize_i8_python",
]


_requested = os.environ.get("MJJ_ACCEL", "1") != "0"
_jit_bm25 = None

if not _requested:
    ACCEL_ENABLED = False
    ACCEL_REASON = "disabled by MJJ_ACCEL=0"
    bm25_accumulate = bm25_accumulate_python
    quantize_i8 = quantize_i8_python
else:
    # Accel is requested: build zero-copy kernel buffers and try ABI/JIT.
    # Missing Mojo still degrades to the pure-Python kernels below.
    ACCEL_ENABLED = True
    try:
        from mojosub import jit
    except Exception as exc:  # noqa: BLE001 - acceleration must be optional
        ACCEL_REASON = (
            f"ABI preferred; mojosub unavailable: {type(exc).__name__}: {exc}"
        )
        quantize_i8 = quantize_i8_python
    else:
        ACCEL_REASON = "mojosub tiered JIT enabled"
        # Both kernels mutate output buffers, so verification checks the return
        # value and every write before the native variant is trusted.
        quantize_i8 = jit(
            quantize_i8_python,
            verify=True,
        )
        _jit_bm25 = jit(
            bm25_accumulate_python,
            verify=True,
            # Optimised Mojo contracts multiply/add operations differently
            # from CPython by one or two ulps.  O0 remains ~15x faster for the
            # measured 5,000-row posting and preserves exact score bits.
            opt=0,
            vectorize=False,
        )

    def bm25_accumulate(
        document_ids: Sequence[int],
        frequencies: Sequence[int],
        lengths: Sequence[float],
        scores: Sequence[float],
        average_length: float,
        k1: float,
        b: float,
        inverse_frequency: float,
        query_boost: float,
    ) -> int:
        # Lazy import: vectors imports quantize_i8 from this package.
        from mjj.search.vectors import native_bm25_accumulate

        native = native_bm25_accumulate(
            document_ids,
            frequencies,
            lengths,
            scores,
            average_length,
            k1,
            b,
            inverse_frequency,
            query_boost,
        )
        if native is not None:
            return native
        if _jit_bm25 is not None:
            return _jit_bm25(
                document_ids,
                frequencies,
                lengths,
                scores,
                average_length,
                k1,
                b,
                inverse_frequency,
                query_boost,
            )
        return bm25_accumulate_python(
            document_ids,
            frequencies,
            lengths,
            scores,
            average_length,
            k1,
            b,
            inverse_frequency,
            query_boost,
        )

    if _jit_bm25 is not None:
        bm25_accumulate.wait = getattr(_jit_bm25, "wait", None)  # type: ignore[attr-defined]
