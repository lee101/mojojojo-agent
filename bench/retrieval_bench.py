"""Adversarial retrieval latency, culling, fallback, and schema measurements."""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mjj.ledger import Budget, Ledger, estimate_tokens  # noqa: E402
from mjj.repo_map import render_repo_map  # noqa: E402
from mjj.search.index import build_index  # noqa: E402
from mjj.tools import build_registry  # noqa: E402
from mjj.tools.base import ToolContext  # noqa: E402
from mjj.tools.search import SearchTool  # noqa: E402


def _median(call, iterations: int) -> tuple[float, object]:
    timings = []
    result = None
    for _ in range(iterations):
        started = time.perf_counter()
        result = call()
        timings.append((time.perf_counter() - started) * 1000)
    return statistics.median(timings), result


def benchmark(iterations: int = 20) -> dict:
    with tempfile.TemporaryDirectory(prefix="mjj-retrieval-") as temporary:
        root = Path(temporary)
        (root / ".gitignore").write_text("ignored.py\noversize.log\n")
        for number in range(120):
            (root / f"worker_{number:03}.py").write_text(
                f"def shared_worker_{number:03}():\n"
                f"    return shared_result  # {number}\n"
            )
        (root / "exact.py").write_text(
            "def rare_retrieval_beacon():\n    return 42\n"
        )
        (root / "ignored.py").write_text("ignored_quasar_beacon = True\n")
        (root / "oversize.log").write_text(
            "x" * (2 * 1024 * 1024 + 1) + "\nzqxvneedle\n"
        )
        (root / "binary.dat").write_bytes(b"ignored_quasar_beacon\0hidden")
        index = build_index(root)

        exact_ms, exact_hits = _median(
            lambda: index.search("rare_retrieval_beacon", limit=8), iterations
        )
        variant_ms, variant_hits = _median(
            lambda: index.search("rareRetrievalBeacon", limit=8), iterations
        )
        ledger = Ledger(Budget(default=64, search=64))
        context = ToolContext(root, ledger)
        first = SearchTool().run(
            {"query": "shared_result", "limit": 8}, context
        )
        second = SearchTool().run(
            {
                "query": "shared_result",
                "limit": 8,
                "cursor": first.meta["next_cursor"],
            },
            context,
        )
        fallback_ms, fallback = _median(
            lambda: SearchTool().run(
                {"query": "zqxvneedle"},
                ToolContext(root, Ledger(Budget(search=64))),
            ),
            max(2, iterations // 4),
        )
        map_ms, repo_map = _median(
            lambda: render_repo_map(
                index,
                query="shared result",
                character_budget=256,
            ),
            iterations,
        )
        raw = "\n".join(
            f"worker_{number:03}.py:2:    return shared_result  # {number}"
            for number in range(120)
        )
        schemas = {item["name"]: item for item in build_registry().schemas()}
        check_schema_tokens = estimate_tokens(
            json.dumps(schemas["check"], separators=(",", ":"))
        )
        map_parameter_tokens = estimate_tokens(
            json.dumps(
                {
                    key: schemas["list"]["parameters"]["properties"][key]
                    for key in ("symbols", "query")
                },
                separators=(",", ":"),
            )
        )
        checkpoint_schema_tokens = estimate_tokens(
            json.dumps(schemas["checkpoint"], separators=(",", ":"))
        )
        navigate_schema_tokens = estimate_tokens(
            json.dumps(schemas["navigate"], separators=(",", ":"))
        )
        shell_job_parameter_tokens = estimate_tokens(
            json.dumps(
                {
                    key: schemas["shell"]["parameters"]["properties"][key]
                    for key in ("background", "job")
                },
                separators=(",", ":"),
            )
        )
        format_parameter_tokens = estimate_tokens(
            json.dumps(
                {
                    "format": schemas["check"]["parameters"]["properties"][
                        "format"
                    ]
                },
                separators=(",", ":"),
            )
        )
        raw_symbols = "\n".join(
            f"worker_{number:03}.py:1:def shared_worker_{number:03}():"
            for number in range(120)
        )
        return {
            "corpus": {
                "indexed_files": index.stats.files,
                "indexed_chunks": index.stats.chunks,
                "excluded_large_bytes": (root / "oversize.log").stat().st_size,
            },
            "latency_ms": {
                "exact_auto_median": round(exact_ms, 3),
                "naming_variant_median": round(variant_ms, 3),
                "large_fallback_median": round(fallback_ms, 3),
                "repository_map_median": round(map_ms, 3),
            },
            "ranking": {
                "exact_sources": list(exact_hits[0].sources),
                "variant_sources": list(variant_hits[0].sources),
                "fallback_strategy": fallback.meta["strategy"],
                "fallback_path": fallback.output.split(":", 1)[0],
                "binary_leaked": "binary.dat" in fallback.output,
            },
            "tokens": {
                "raw_120_matches": estimate_tokens(raw),
                "first_page": estimate_tokens(first.output),
                "second_page": estimate_tokens(second.output),
                "withheld_after_two_pages": max(
                    0,
                    estimate_tokens(raw)
                    - estimate_tokens(first.output)
                    - estimate_tokens(second.output),
                ),
                "check_tool_schema": check_schema_tokens,
                "map_parameters_schema": map_parameter_tokens,
                "checkpoint_tool_schema": checkpoint_schema_tokens,
                "navigate_tool_schema": navigate_schema_tokens,
                "shell_job_parameters_schema": shell_job_parameter_tokens,
                "format_parameter_schema": format_parameter_tokens,
                "raw_symbol_listing": estimate_tokens(raw_symbols),
                "repository_map": estimate_tokens(repo_map.output),
            },
            "continuation": {
                "first_cursor": first.meta["next_cursor"],
                "second_cursor": second.meta["next_cursor"],
                "pages_differ": first.output != second.output,
            },
            "repository_map": {
                "files": repo_map.files,
                "symbols": repo_map.symbols,
                "omitted_files": repo_map.omitted_files,
            },
        }


def main() -> int:
    report = benchmark()
    print(json.dumps(report, indent=2))
    ranking = report["ranking"]
    continuation = report["continuation"]
    return 0 if (
        ranking["exact_sources"] == ["literal"]
        and ranking["fallback_strategy"] == "fallback"
        and not ranking["binary_leaked"]
        and continuation["pages_differ"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
