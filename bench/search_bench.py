"""Reproducible search measurements on the real mojojojo repository."""

from __future__ import annotations

import argparse
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from mjj.ledger import estimate_tokens  # noqa: E402
from mjj.search.index import build_index  # noqa: E402


DEFAULT_CORPUS = Path("/nvme0n1-disk/code/mojojojo")
QUERIES = (
    "errInsufficientCredits",
    "workerBootstrap",
    "mojojail",
    "billed_ms",
)


def _median_ms(function, repeats: int) -> tuple[float, str]:
    samples: list[float] = []
    value = ""
    for _ in range(repeats):
        started = time.perf_counter()
        value = function()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples), value


def _rg(corpus: Path, query: str) -> str:
    if not shutil.which("rg"):
        return ""
    completed = subprocess.run(
        [
            "rg", "-n", "--no-heading", "--color", "never",
            "-F", query, ".",
        ],
        cwd=corpus,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    return completed.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args(argv)
    corpus = args.corpus.resolve()
    if not corpus.is_dir():
        parser.error(f"corpus is not a directory: {corpus}")
    repeats = max(1, args.repeats)

    with tempfile.TemporaryDirectory(prefix="mjj-search-bench-") as temporary:
        index_path = Path(temporary) / ".mjj" / "index"
        started = time.perf_counter()
        index = build_index(corpus, index_path=index_path, force=True)
        build_ms = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        index = build_index(corpus, index_path=index_path, existing=index)
        incremental_ms = (time.perf_counter() - started) * 1000

        print(f"Corpus: `{corpus}`  ")
        print(
            f"Index: {index.stats.files} files, {index.stats.chunks} chunks, "
            f"{index.backend_name} backend; query time is median of "
            f"{repeats} runs."
        )
        print()
        print("| Case | MJJ time | rg time | MJJ tokens | rg tokens |")
        print("|---|---:|---:|---:|---:|")
        print(f"| Fresh index build | {build_ms:.2f} ms | — | — | — |")
        print(
            f"| Unchanged incremental index | {incremental_ms:.2f} ms "
            "| — | — | — |"
        )

        for query in QUERIES:
            # Warm both paths before collecting medians.
            index.format_hits(index.search(query, limit=8))
            _rg(corpus, query)
            search_ms, search_output = _median_ms(
                lambda query=query: index.format_hits(
                    index.search(query, limit=8)
                ),
                repeats,
            )
            if shutil.which("rg"):
                rg_ms, rg_output = _median_ms(
                    lambda query=query: _rg(corpus, query),
                    repeats,
                )
                rg_time = f"{rg_ms:.2f} ms"
                rg_tokens = str(estimate_tokens(rg_output))
            else:
                rg_time = "unavailable"
                rg_tokens = "—"
            print(
                f"| `{query}` | {search_ms:.2f} ms | {rg_time} | "
                f"{estimate_tokens(search_output)} | {rg_tokens} |"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
