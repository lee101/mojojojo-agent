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
    assert report["continuation"]["pages_differ"]
