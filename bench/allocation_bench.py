"""Reproducible latency and peak-allocation checks for search hot paths."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import statistics
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("MJJ_BENCH_SOURCE", ROOT)).resolve()
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from mjj.search.index import build_index  # noqa: E402
from mjj.search.lexical import tokenize, tokenize_python  # noqa: E402
from mjj.search.vectors import (  # noqa: E402
    native_static_embedding_tokens,
    native_tokenize,
    static_embedding,
    static_embedding_tokens_python,
)

SAMPLE = "\n".join(
    f"def HTTPServer{index}.refresh_access_token(worker_{index}): "
    f"return workerBootstrap{index} and cached_result"
    for index in range(80)
)


def _median_us(call: Callable[[], object], loops: int, repeats: int = 7) -> float:
    call()
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter_ns()
        for _ in range(loops):
            call()
        samples.append((time.perf_counter_ns() - started) / loops / 1_000.0)
    return statistics.median(samples)


def _peak_bytes(call: Callable[[], object], repeats: int = 7) -> int:
    peaks: list[int] = []
    call()
    for _ in range(repeats):
        gc.collect()
        tracemalloc.start(1)
        before = tracemalloc.get_traced_memory()[0]
        result = call()
        peak = tracemalloc.get_traced_memory()[1]
        peaks.append(max(0, peak - before))
        del result
        tracemalloc.stop()
    return int(statistics.median(peaks))


def benchmark(iterations: int = 100) -> dict:
    loops = max(1, int(iterations))
    embedding_loops = max(1, loops // 20)
    sample_tokens = tokenize_python(SAMPLE)
    latency = {
        "tokenize_python": round(
            _median_us(lambda: tokenize_python(SAMPLE), loops), 3
        ),
        "static_embedding_python": round(
            _median_us(
                lambda: static_embedding_tokens_python(sample_tokens),
                embedding_loops,
            ),
            3,
        ),
        "tokenize": round(_median_us(lambda: tokenize(SAMPLE), loops), 3),
        "static_embedding": round(
            _median_us(
                lambda: static_embedding(SAMPLE),
                embedding_loops,
            ),
            3,
        ),
    }
    peak = {
        "tokenize_python": _peak_bytes(lambda: tokenize_python(SAMPLE)),
        "static_embedding_python": _peak_bytes(
            lambda: static_embedding_tokens_python(sample_tokens)
        ),
        "tokenize": _peak_bytes(lambda: tokenize(SAMPLE)),
        "static_embedding": _peak_bytes(lambda: static_embedding(SAMPLE)),
    }
    if native_tokenize(SAMPLE) is not None:
        latency["tokenize_mojo"] = latency["tokenize"]
        peak["tokenize_mojo"] = peak["tokenize"]
    if native_static_embedding_tokens(sample_tokens) is not None:
        latency["static_embedding_mojo"] = round(
            _median_us(
                lambda: native_static_embedding_tokens(sample_tokens),
                embedding_loops,
            ),
            3,
        )
        peak["static_embedding_mojo"] = _peak_bytes(
            lambda: native_static_embedding_tokens(sample_tokens)
        )
    return {
        "sample_chars": len(SAMPLE),
        "latency_us": latency,
        "peak_bytes": peak,
    }


def profile_index(corpus: Path, repeats: int = 3) -> dict:
    timings: list[float] = []
    for _ in range(max(1, repeats)):
        with tempfile.TemporaryDirectory(prefix="mjj-allocation-time-") as temporary:
            started = time.perf_counter()
            build_index(
                corpus,
                index_path=Path(temporary) / "index",
                force=True,
            )
            timings.append((time.perf_counter() - started) * 1_000.0)

    with tempfile.TemporaryDirectory(prefix="mjj-allocation-peak-") as temporary:
        gc.collect()
        tracemalloc.start(1)
        index = build_index(
            corpus,
            index_path=Path(temporary) / "index",
            force=True,
        )
        current, peak = tracemalloc.get_traced_memory()
        snapshot = tracemalloc.take_snapshot()
        tracemalloc.stop()
        digest = hashlib.sha256()
        for relative in sorted(index.files):
            digest.update(relative.encode("utf-8", "surrogatepass"))
            digest.update(b"\0")
            try:
                digest.update((corpus / relative).read_bytes())
            except OSError:
                digest.update(b"<unreadable>")
            digest.update(b"\0")
        top = []
        for statistic in snapshot.statistics("lineno"):
            frame = statistic.traceback[0]
            try:
                path = Path(frame.filename).resolve().relative_to(SOURCE)
            except ValueError:
                continue
            top.append(
                {
                    "location": f"{path}:{frame.lineno}",
                    "bytes": statistic.size,
                    "blocks": statistic.count,
                }
            )
            if len(top) == 12:
                break
        return {
            "files": index.stats.files,
            "chunks": index.stats.chunks,
            "corpus_sha256": digest.hexdigest(),
            "build_median_ms": round(statistics.median(timings), 3),
            "retained_bytes": current,
            "peak_bytes": peak,
            "top_retained": top,
        }


def markdown(report: dict) -> str:
    lines = [
        "| hot path | median latency | peak traced bytes |",
        "| --- | ---: | ---: |",
    ]
    for name in (
        "tokenize_python",
        "tokenize_mojo",
        "tokenize",
        "static_embedding_python",
        "static_embedding_mojo",
        "static_embedding",
    ):
        if name not in report["latency_us"]:
            continue
        lines.append(
            f"| `{name}` | {report['latency_us'][name]:.3f} us | "
            f"{report['peak_bytes'][name]} |"
        )
    index = report.get("index")
    if index:
        lines.extend(
            (
                "",
                (
                    f"Cold index: {index['files']} files / "
                    f"{index['chunks']} chunks, median "
                    f"{index['build_median_ms']:.3f} ms, peak traced heap "
                    f"{index['peak_bytes']} bytes."
                ),
                f"Corpus SHA-256: `{index['corpus_sha256']}`.",
                "",
                "| retained allocation site | bytes | blocks |",
                "| --- | ---: | ---: |",
            )
        )
        lines.extend(
            f"| `{item['location']}` | {item['bytes']} | {item['blocks']} |"
            for item in index["top_retained"]
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--index-repeats", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--workload-only",
        action="store_true",
        help="run the hot calls without measurement output for memray",
    )
    args = parser.parse_args(argv)

    if args.workload_only:
        for _ in range(max(1, args.iterations)):
            tokenize(SAMPLE)
            static_embedding(SAMPLE)
        return 0

    report = benchmark(args.iterations)
    if args.corpus:
        corpus = args.corpus.resolve()
        if not corpus.is_dir():
            parser.error(f"corpus is not a directory: {corpus}")
        report["index"] = profile_index(corpus, args.index_repeats)
    print(json.dumps(report, indent=2) if args.json else markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
