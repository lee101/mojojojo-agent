from bench.startup_bench import measure


def test_startup_benchmark_reports_all_bounded_cases() -> None:
    results = measure(repeats=1)

    assert set(results) == {"version", "help", "stdlib_ui_exit"}
    assert all(values["median_ms"] > 0 for values in results.values())
    assert all(values["p95_ms"] >= values["median_ms"] for values in results.values())
