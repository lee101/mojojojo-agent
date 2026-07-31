"""Optional mojosub acceleration for the harness's measured hot loops.

The functions exported here always work.  With mojosub installed they execute
in CPython first and compile in the background; without it, or with
``MJJ_ACCEL=0``, they are the same pure-Python functions directly.
"""

from __future__ import annotations

import os

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
if not _requested:
    ACCEL_ENABLED = False
    ACCEL_REASON = "disabled by MJJ_ACCEL=0"
    bm25_accumulate = bm25_accumulate_python
    quantize_i8 = quantize_i8_python
else:
    try:
        from mojosub import jit
    except Exception as exc:  # noqa: BLE001 - acceleration must be optional
        ACCEL_ENABLED = False
        ACCEL_REASON = f"mojosub unavailable: {type(exc).__name__}: {exc}"
        bm25_accumulate = bm25_accumulate_python
        quantize_i8 = quantize_i8_python
    else:
        ACCEL_ENABLED = True
        ACCEL_REASON = "mojosub tiered JIT enabled"
        # Both kernels mutate output buffers, so verification checks the return
        # value and every write before the native variant is trusted.
        quantize_i8 = jit(
            quantize_i8_python,
            verify=True,
        )
        bm25_accumulate = jit(
            bm25_accumulate_python,
            verify=True,
            # Optimised Mojo contracts multiply/add operations differently
            # from CPython by one or two ulps.  O0 remains ~15x faster for the
            # measured 5,000-row posting and preserves exact score bits.
            opt=0,
            vectorize=False,
        )
