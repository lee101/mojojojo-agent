"""Reproducible measurements for retained and rejected kernel candidates.

Run the compact benchmark:

    python -m mjj.kernels.bench

Add real repositories to repeat the cold-index/search profile:

    python -m mjj.kernels.bench \
      --profile-root /path/to/mojojojo --profile-root /path/to/openpaths
"""

from __future__ import annotations

import argparse
import cProfile
import math
import pstats
import random
import statistics
import tempfile
import time
from array import array
from pathlib import Path

from mjj.kernels import (
    ACCEL_ENABLED,
    ACCEL_REASON,
    bm25_accumulate,
    bm25_accumulate_python,
    quantize_i8,
)
from mjj.ledger import Budget, Ledger
from mjj.search.index import build_index
from mjj.search.lexical import tokenize
from mjj.search.vectors import Int8Vectors, quantize
from mjj.tools.patch import _find_block

QUERIES = (
    "auth token refresh",
    "execute sandbox worker",
    "credit ledger app_id",
    "session cookie handler",
    "search vector index",
    "compile mojo source",
    "http server request",
    "database transaction",
    "api key validation",
    "stream response error",
    "repository path config",
    "user account login",
)


def _median_us(function, *, loops: int, repeats: int = 7) -> float:
    samples = []
    for _ in range(repeats):
        started = time.perf_counter_ns()
        for _ in range(loops):
            function()
        samples.append((time.perf_counter_ns() - started) / loops / 1_000.0)
    return statistics.median(samples)


def _wait(function) -> None:
    wait = getattr(function, "wait", None)
    if wait is not None:
        wait(120.0)


def _reference_quantize(values: list[float]) -> tuple[bytes, float]:
    norm = math.sqrt(sum(float(value) * float(value) for value in values))
    peak = max((abs(float(value)) for value in values), default=0.0)
    scale = peak / 127.0 if peak else 1.0
    inverse = 1.0 / scale
    output = array(
        "b",
        (
            max(
                -127,
                min(
                    127,
                    int(
                        value * inverse
                        + (0.5 if value >= 0.0 else -0.5)
                    ),
                ),
            )
            for value in values
        ),
    )
    return output.tobytes(), scale / norm if norm else 1.0


class _MissingBackend:
    available = False


def compact_benchmark() -> None:
    rng = random.Random(0x4D4A4A)
    values = [rng.uniform(-2.0, 2.0) for _ in range(256)]
    numeric = array("d", values)
    quantized = array("q", [0]) * len(numeric)
    quantize_i8(numeric, quantized)

    count = 10_000
    document_ids = array("q", range(5_000))
    frequencies = array("q", (1 + index % 7 for index in range(5_000)))
    lengths = array("d", (20.0 + index % 300 for index in range(count)))
    scores = array("d", [0.0]) * count
    parameters = (150.0, 1.2, 0.72, 0.8, 1.0)
    bm25_accumulate(
        document_ids, frequencies, lengths, scores, *parameters
    )
    _wait(quantize_i8)
    _wait(bm25_accumulate)
    # Settle mojosub's one-time verify/race before collecting hot samples.
    quantize_i8(numeric, array("q", [0]) * len(numeric))
    bm25_accumulate(
        document_ids,
        frequencies,
        lengths,
        array("d", [0.0]) * count,
        *parameters,
    )

    rows: list[tuple[str, str, float, float | None, str]] = []
    reference = _median_us(
        lambda: _reference_quantize(values), loops=1_000
    )
    accelerated = _median_us(lambda: quantize(values), loops=1_000)
    assert _reference_quantize(values) == quantize(values)
    rows.append(
        (
            "quantize 256, end-to-end",
            "numeric",
            reference,
            accelerated,
            "keep" if accelerated < reference else "loss",
        )
    )

    pure_scores = array("d", [0.0]) * count
    native_scores = array("d", [0.0]) * count
    pure = _median_us(
        lambda: bm25_accumulate_python(
            document_ids,
            frequencies,
            lengths,
            pure_scores,
            *parameters,
        ),
        loops=40,
    )
    accelerated = _median_us(
        lambda: bm25_accumulate(
            document_ids,
            frequencies,
            lengths,
            native_scores,
            *parameters,
        ),
        loops=40,
    )
    rows.append(
        (
            "BM25 posting, 5,000 rows",
            "numeric",
            pure,
            accelerated,
            "keep" if accelerated < pure else "loss",
        )
    )

    text = " ".join(
        f"HTTPServer{index}.fetch_user auth_token path/to/source.py"
        for index in range(400)
    )
    rows.append(
        (
            "identifier tokenizer",
            "strings (unsupported)",
            _median_us(lambda: tokenize(text), loops=100),
            None,
            "reject",
        )
    )

    clipped_text = "\n".join(f"{index}: output line" for index in range(5_000))
    ledger = Ledger(Budget(default=600))
    rows.append(
        (
            "ledger head/tail clip",
            "strings (unsupported)",
            _median_us(
                lambda: ledger.clip("shell", clipped_text), loops=200
            ),
            None,
            "reject",
        )
    )

    lines = [f"line {index}" for index in range(20_000)]
    wanted = [line + "  " for line in lines[-8:]]
    rows.append(
        (
            "fuzzy patch alignment",
            "strings (unsupported)",
            _median_us(
                lambda: _find_block(lines, wanted, 0, False), loops=5
            ),
            None,
            "reject",
        )
    )

    vector_count = 500
    vector_data = bytes(
        rng.randrange(256) for _ in range(vector_count * 256)
    )
    factors = array("f", [1.0 / 127.0]) * vector_count
    query = bytes(rng.randrange(256) for _ in range(256))
    vectors = Int8Vectors(
        vector_data,
        factors,
        backend=_MissingBackend(),  # type: ignore[arg-type]
    )
    rows.append(
        (
            "int8 top-k fallback, 500 rows",
            "int8 (unsupported)",
            _median_us(lambda: vectors.search(query, 1.0, 20), loops=5),
            None,
            "use mojo-embed",
        )
    )

    print(f"acceleration: {ACCEL_ENABLED} ({ACCEL_REASON})")
    print("| candidate | shape | Python µs | accelerated µs | decision |")
    print("| --- | --- | ---: | ---: | --- |")
    for name, shape, python_us, accelerated_us, decision in rows:
        native = "n/a" if accelerated_us is None else f"{accelerated_us:.1f}"
        print(
            f"| {name} | {shape} | {python_us:.1f} | "
            f"{native} | {decision} |"
        )


def _cumulative(profile: cProfile.Profile, function_name: str) -> float:
    stats = pstats.Stats(profile)
    return sum(
        values[3]
        for (_, _, name), values in stats.stats.items()
        if name == function_name
    )


def real_profile(
    roots: list[Path],
    search_repeats: int,
    *,
    reference: bool,
) -> None:
    if reference:
        # `encode` resolves this module global at call time, so this restores
        # the pre-kernel quantisation path without maintaining a second indexer.
        from mjj.search import vectors as vector_module

        vector_module.quantize = _reference_quantize
    with tempfile.TemporaryDirectory(prefix="mjj-kernels-bench-") as temporary:
        indexes = []
        profile = cProfile.Profile()
        profile.enable()
        started = time.perf_counter()
        for number, root in enumerate(roots):
            indexes.append(
                build_index(
                    root,
                    index_path=Path(temporary) / f"index-{number}",
                    force=True,
                )
            )
        index_seconds = time.perf_counter() - started
        profile.disable()
        print(
            "profile indexes:",
            [(item.stats.files, item.stats.chunks) for item in indexes],
        )
        print(f"index total: {index_seconds:.3f}s")
        for name in (
            "tokenize",
            "quantize",
            "_reference_quantize",
            "static_embedding",
            "static_embedding_tokens",
            "encode_tokens",
        ):
            print(f"  {name}: {_cumulative(profile, name):.3f}s cumulative")

        for index in indexes:
            index.lexical()
        profile = cProfile.Profile()
        profile.enable()
        started = time.perf_counter()
        calls = 0
        for _ in range(search_repeats):
            for index in indexes:
                for query in QUERIES:
                    index.search(query, mode="auto", limit=10)
                    calls += 1
        search_seconds = time.perf_counter() - started
        profile.disable()
        print(f"search total: {search_seconds:.3f}s for {calls} auto searches")
        for name in ("_literal_rg", "search", "quantize"):
            print(f"  {name}: {_cumulative(profile, name):.3f}s cumulative")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile-root",
        action="append",
        default=[],
        type=Path,
        help="repository root to index and search (repeatable)",
    )
    parser.add_argument("--search-repeats", type=int, default=20)
    parser.add_argument(
        "--reference",
        action="store_true",
        help="restore the pre-kernel quantizer for the real-workload profile",
    )
    args = parser.parse_args(argv)
    compact_benchmark()
    if args.profile_root:
        real_profile(
            args.profile_root,
            max(1, args.search_repeats),
            reference=args.reference,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
