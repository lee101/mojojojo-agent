from __future__ import annotations

import math
import os
import random
import subprocess
import sys
from array import array
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from mjj.kernels import (
    bm25_accumulate,
    bm25_accumulate_python,
    quantize_i8,
    quantize_i8_python,
)
from mjj.search import lexical as lexical_module
from mjj.search.lexical import LexicalIndex


def _wait_for_native(function) -> None:
    wait = getattr(function, "wait", None)
    if wait is not None:
        assert wait(60.0)


def _quantize_reference(values: list[float]) -> tuple[list[int], float]:
    peak = max((abs(float(value)) for value in values), default=0.0)
    scale = peak / 127.0 if peak else 1.0
    inverse = 1.0 / scale
    output = [
        max(
            -127,
            min(
                127,
                int(value * inverse + (0.5 if value >= 0.0 else -0.5)),
            ),
        )
        for value in values
    ]
    return output, scale


def test_quantize_kernel_exact_randomised() -> None:
    rng = random.Random(0x4D4A4A)
    cases = [
        [],
        [0.0],
        [-1.0, 0.0, 1.0],
        *[
            [rng.uniform(-100.0, 100.0) for _ in range(rng.randrange(1, 513))]
            for _ in range(80)
        ],
    ]

    # Start a tiered build, if acceleration is installed, before checking the
    # whole corpus.  A missing/broken toolchain simply leaves this on Python.
    probe = array("d", cases[-1])
    quantize_i8(probe, array("q", [0]) * len(probe))
    _wait_for_native(quantize_i8)

    for values in cases:
        expected, expected_scale = _quantize_reference(values)
        numeric = array("d", values)
        pure_output = array("q", [0]) * len(values)
        public_output = array("q", [0]) * len(values)
        assert quantize_i8_python(numeric, pure_output) == expected_scale
        assert quantize_i8(numeric, public_output) == expected_scale
        assert list(pure_output) == expected
        assert list(public_output) == expected


def _bm25_reference(
    document_ids: array,
    frequencies: array,
    lengths: array,
    scores: array,
    average_length: float,
    k1: float,
    b: float,
    inverse_frequency: float,
    query_boost: float,
) -> None:
    for document_id, frequency in zip(document_ids, frequencies):
        normalizer = k1 * (
            1.0 - b + b * lengths[document_id] / average_length
        )
        scores[document_id] += (
            inverse_frequency
            * (frequency * (k1 + 1.0))
            / (frequency + normalizer)
            * query_boost
        )


def test_bm25_kernel_exact_randomised() -> None:
    rng = random.Random(0xB025)
    for case in range(50):
        count = rng.randrange(1, 800)
        posting_size = rng.randrange(0, count + 1)
        document_ids = array("q", rng.sample(range(count), posting_size))
        frequencies = array(
            "q", (rng.randrange(1, 12) for _ in range(posting_size))
        )
        lengths = array(
            "d", (float(rng.randrange(1, 500)) for _ in range(count))
        )
        average_length = sum(lengths) / len(lengths)
        parameters = (
            average_length,
            rng.uniform(0.5, 2.0),
            rng.uniform(0.0, 1.0),
            rng.uniform(0.01, 8.0),
            rng.uniform(1.0, 4.0),
        )
        expected = array("d", [0.0]) * count
        pure = array("d", [0.0]) * count
        public = array("d", [0.0]) * count
        _bm25_reference(
            document_ids, frequencies, lengths, expected, *parameters
        )
        assert bm25_accumulate_python(
            document_ids, frequencies, lengths, pure, *parameters
        ) == posting_size
        assert bm25_accumulate(
            document_ids, frequencies, lengths, public, *parameters
        ) == posting_size
        assert pure == expected
        assert public == expected
        if case == 0:
            _wait_for_native(bm25_accumulate)


def _reference_search(
    index: LexicalIndex,
    terms: list[str],
    limit: int,
    k1: float,
    b: float,
) -> list[tuple[int, float]]:
    query_terms = Counter(terms)
    scores: dict[int, float] = defaultdict(float)
    count = len(index.documents)
    for term, query_frequency in query_terms.items():
        posting = index.postings.get(term)
        if not posting:
            continue
        document_frequency = len(posting)
        inverse_frequency = math.log(
            1.0 + (count - document_frequency + 0.5)
            / (document_frequency + 0.5)
        )
        query_boost = 1.0 + math.log(query_frequency)
        for document_id, frequency in posting:
            normalizer = k1 * (
                1.0 - b + b * index.lengths[document_id]
                / index.average_length
            )
            scores[document_id] += (
                inverse_frequency
                * (frequency * (k1 + 1.0))
                / (frequency + normalizer)
                * query_boost
            )
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]


def test_lexical_kernel_path_matches_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lexical_module, "ACCEL_ENABLED", True)
    monkeypatch.setattr(
        lexical_module, "bm25_accumulate", bm25_accumulate_python
    )
    rng = random.Random(711)
    documents = [
        {
            f"term{term}": rng.randrange(1, 8)
            for term in rng.sample(range(30), rng.randrange(1, 12))
        }
        for _ in range(400)
    ]
    index = LexicalIndex(documents)
    for _ in range(80):
        terms = [
            f"term{rng.randrange(30)}" for _ in range(rng.randrange(1, 8))
        ]
        assert index.search(terms, 25) == _reference_search(
            index, terms, 25, 1.2, 0.72
        )


def test_mjj_accel_zero_avoids_importing_mojosub() -> None:
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["MJJ_ACCEL"] = "0"
    environment["PYTHONPATH"] = str(root)
    code = (
        "import sys; "
        "from mjj.kernels import ACCEL_ENABLED, ACCEL_REASON, quantize_i8, "
        "quantize_i8_python; "
        "assert not ACCEL_ENABLED; "
        "assert ACCEL_REASON == 'disabled by MJJ_ACCEL=0'; "
        "assert quantize_i8 is quantize_i8_python; "
        "assert 'mojosub' not in sys.modules"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
