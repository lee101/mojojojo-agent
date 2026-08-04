from bench.retrieval_bench import benchmark


def test_adversarial_retrieval_benchmark_guards_efficiency_and_fallback() -> None:
    report = benchmark(iterations=2)

    assert report["ranking"]["exact_sources"] == ["literal"]
    assert "semantic" in report["ranking"]["variant_sources"]
    assert report["ranking"]["fallback_strategy"] == "fallback"
    assert report["ranking"]["fallback_path"] == "oversize.log"
    assert not report["ranking"]["binary_leaked"]
    assert report["tokens"]["first_page"] < report["tokens"]["raw_120_matches"] // 10
    assert report["tokens"]["withheld_after_two_pages"] > 1_000
    assert report["tokens"]["repository_map"] < report["tokens"]["raw_symbol_listing"] // 5
    assert report["tokens"]["checkpoint_tool_schema"] < 90
    assert report["tokens"]["navigate_tool_schema"] < 130
    assert report["tokens"]["shell_job_parameters_schema"] < 50
    assert report["tokens"]["format_parameter_schema"] < 30
    assert report["repository_map"]["omitted_files"] > 0
    assert report["continuation"]["pages_differ"]
