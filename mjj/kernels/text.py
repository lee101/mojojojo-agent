"""Pure-Python definitions for numeric loops selected by profiling.

The annotations use mojosub's supported flat-buffer vocabulary.  CPython is
equally happy to receive ``array.array`` objects, which callers use so the
native tier can borrow their storage instead of copying Python lists.
"""

from __future__ import annotations

def quantize_i8_python(values: list[float], output: list[int]) -> float:
    """Write symmetric int8 values and return their quantisation scale."""
    peak = 0.0
    for index in range(len(values)):
        value = values[index]
        magnitude = abs(value)
        if magnitude > peak:
            peak = magnitude

    scale = peak / 127.0 if peak else 1.0
    inverse = 1.0 / scale
    for index in range(len(values)):
        value = values[index]
        rounded = int(
            value * inverse + (0.5 if value >= 0.0 else -0.5)
        )
        if rounded < -127:
            rounded = -127
        elif rounded > 127:
            rounded = 127
        output[index] = rounded

    return scale


def bm25_accumulate_python(
    document_ids: list[int],
    frequencies: list[int],
    lengths: list[float],
    scores: list[float],
    average_length: float,
    k1: float,
    b: float,
    inverse_frequency: float,
    query_boost: float,
) -> int:
    """Accumulate one term's BM25 contribution into a dense score buffer."""
    for index in range(len(document_ids)):
        document_id = document_ids[index]
        frequency = frequencies[index]
        normalizer = k1 * (
            1.0 - b + b * lengths[document_id] / average_length
        )
        scores[document_id] += (
            inverse_frequency
            * (frequency * (k1 + 1.0))
            / (frequency + normalizer)
            * query_boost
        )
    return len(document_ids)
