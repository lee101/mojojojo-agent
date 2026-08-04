from bench.allocation_bench import benchmark, markdown


def test_allocation_benchmark_is_bounded_and_markdown_renderable() -> None:
    report = benchmark(iterations=2)

    assert report["sample_chars"] > 1_000
    assert report["latency_us"]["tokenize"] > 0
    assert report["latency_us"]["static_embedding"] > 0
    assert report["peak_bytes"]["tokenize"] > 0
    assert report["peak_bytes"]["static_embedding"] > 0
    rendered = markdown(report)
    assert "peak traced bytes" in rendered
    assert "`static_embedding`" in rendered
