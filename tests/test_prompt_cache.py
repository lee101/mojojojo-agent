from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from mjj.prompt_cache import MIN_CACHEABLE_CHARS, PromptCacheOptimizer, _decide_ttl


LONG_INSTRUCTIONS = "stable agent contract " * (MIN_CACHEABLE_CHARS // 16)


def test_ttl_optimizer_matches_reuse_cadence() -> None:
    minute = 60.0
    assert _decide_ttl([0], cold="5m") == "5m"
    assert _decide_ttl([0, minute, 2 * minute, 3 * minute]) == "5m"
    assert _decide_ttl([0, 30 * minute, 60 * minute, 90 * minute]) == "1h"
    assert _decide_ttl([0, 2 * 60 * minute, 4 * 60 * minute]) == "none"


def test_openai_auto_avoids_one_shot_write_then_enables_reused_prefix() -> None:
    optimizer = PromptCacheOptimizer(mode="auto")

    cold = optimizer.openai_plan("gpt-5.6-terra", LONG_INSTRUCTIONS, [], now=0)
    warm = optimizer.openai_plan("gpt-5.6-terra", LONG_INSTRUCTIONS, [], now=60)

    assert cold.mode == "explicit" and not cold.breakpoint
    assert warm.mode == "explicit" and warm.breakpoint and warm.ttl == "30m"
    assert cold.key == warm.key


def test_openai_auto_uses_its_actual_30_minute_reuse_window() -> None:
    within_ttl = PromptCacheOptimizer(mode="auto")
    within_ttl.openai_plan("gpt-5.6-terra", LONG_INSTRUCTIONS, [], now=0)
    useful = within_ttl.openai_plan(
        "gpt-5.6-terra",
        LONG_INSTRUCTIONS,
        [],
        now=20 * 60,
    )

    beyond_ttl = PromptCacheOptimizer(mode="auto")
    beyond_ttl.openai_plan("gpt-5.6-terra", LONG_INSTRUCTIONS, [], now=0)
    wasteful = beyond_ttl.openai_plan(
        "gpt-5.6-terra",
        LONG_INSTRUCTIONS,
        [],
        now=31 * 60,
    )

    assert useful.breakpoint and useful.ttl == "30m"
    assert not wasteful.breakpoint and not wasteful.ttl


def test_small_prefix_never_requests_a_paid_explicit_write() -> None:
    optimizer = PromptCacheOptimizer(mode="explicit")
    plan = optimizer.openai_plan("gpt-5.6-sol", "short", [])

    assert plan.mode == "explicit"
    assert not plan.breakpoint


def test_anthropic_auto_selects_short_long_or_no_cache() -> None:
    optimizer = PromptCacheOptimizer(mode="auto")

    first = optimizer.anthropic_plan("claude-sonnet", LONG_INSTRUCTIONS, [], now=0)
    short = optimizer.anthropic_plan("claude-sonnet", LONG_INSTRUCTIONS, [], now=60)

    assert first.ttl == "5m" and first.breakpoint
    assert short.ttl == "5m" and short.breakpoint

    sparse = PromptCacheOptimizer(mode="auto")
    sparse.anthropic_plan("claude-sonnet", LONG_INSTRUCTIONS, [], now=0)
    sparse_plan = sparse.anthropic_plan(
        "claude-sonnet", LONG_INSTRUCTIONS, [], now=2 * 60 * 60
    )
    assert not sparse_plan.breakpoint and sparse_plan.ttl == "none"


def test_cache_telemetry_is_bounded_and_observable() -> None:
    optimizer = PromptCacheOptimizer()
    optimizer.record(read_tokens=800, write_tokens=100)

    assert optimizer.status() == {
        "mode": "auto",
        "prefixes": 0,
        "cache_read_tokens": 800,
        "cache_write_tokens": 100,
    }


def test_prefix_tracker_evicts_old_entries_at_its_hard_cap() -> None:
    optimizer = PromptCacheOptimizer()
    for index in range(600):
        optimizer.openai_plan(
            f"gpt-5.6-terra-{index}",
            LONG_INSTRUCTIONS,
            [],
            now=float(index),
        )

    assert optimizer.status()["prefixes"] == 512


def test_shared_optimizer_is_safe_for_concurrent_hosted_runs() -> None:
    optimizer = PromptCacheOptimizer()

    def observe(index: int) -> None:
        optimizer.openai_plan(
            f"gpt-5.6-terra-{index % 8}",
            LONG_INSTRUCTIONS,
            [],
            now=float(index),
        )
        optimizer.record(read_tokens=1, write_tokens=1)

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(observe, range(1_000)))

    status = optimizer.status()
    assert status["prefixes"] == 8
    assert status["cache_read_tokens"] == 1_000
    assert status["cache_write_tokens"] == 1_000
