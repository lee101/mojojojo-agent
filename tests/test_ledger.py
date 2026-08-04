"""The truncation policy. This is the file that decides what a run costs."""

from __future__ import annotations

import os
import re

from mjj.ledger import CHARS_PER_TOKEN, Budget, Ledger, estimate_tokens
from mjj.tools.base import Registry, ToolContext, ToolResult


def ledger(**budgets) -> Ledger:
    return Ledger(budget=Budget(**budgets))


def test_short_output_is_untouched():
    led = ledger(default=100)
    assert led.clip("default", "hello") == "hello"
    assert led.drops == []


def test_long_output_keeps_head_and_tail():
    led = ledger(default=40)  # 160 chars
    text = "\n".join(f"line {i}" for i in range(200))
    out = led.clip("shell", text)
    assert out.startswith("line 0")
    assert out.rstrip().endswith("line 199")
    assert "lines omitted" in out
    assert len(out) <= 40 * CHARS_PER_TOKEN + 80


def test_tail_survives_because_errors_live_at_the_end():
    led = ledger(default=60)
    text = "\n".join(["noise"] * 300 + ["Traceback (most recent call last):", "ValueError: boom"])
    out = led.clip("shell", text)
    assert "ValueError: boom" in out


def test_hint_is_shown_so_the_model_can_ask_for_the_rest():
    led = ledger(default=20)
    out = led.clip("read", "x\n" * 500, hint="read lines 200-400")
    assert "read lines 200-400" in out


def test_single_line_blob_is_clipped_in_the_middle():
    led = ledger(default=25)
    out = led.clip("default", "a" * 5000)
    assert "[clipped]" in out and out.startswith("aaa") and out.endswith("aaa")


def test_clipped_output_is_spilled_with_a_retrieval_address(tmp_path):
    led = ledger(default=40)
    ToolContext(tmp_path, led)
    original = "\n".join(f"diagnostic {number}" for number in range(500))

    out = led.clip("shell", original)
    match = re.search(r"\.mjj/tool-results/[\w.-]+\.txt", out)

    assert match is not None
    spilled = tmp_path / match.group(0)
    assert spilled.read_text() == original
    if os.name != "nt":
        assert spilled.stat().st_mode & 0o777 == 0o600


def test_single_line_spill_address_survives_blob_clipping(tmp_path):
    led = ledger(default=40)
    ToolContext(tmp_path, led)

    out = led.clip("shell", "x" * 20_000)

    assert "full output: .mjj/tool-results/" in out


def test_drops_are_recorded():
    led = ledger(default=20)
    led.clip("search", "\n".join(str(i) for i in range(500)))
    assert led.drops and led.drops[0].tool == "search"
    assert led.drops[0].dropped_lines > 0
    assert "withheld" in led.summary()


def test_per_tool_budgets_are_independent():
    led = Ledger(budget=Budget(search=10, read=1000))
    text = "\n".join(f"line {i}" for i in range(300))
    assert len(led.clip("search", text)) < len(led.clip("read", text))


def test_estimate_is_monotonic():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("a" * 4001) > estimate_tokens("a" * 4000)


def test_budget_is_a_hard_bound_even_with_hostile_hints():
    texts = [
        "x" * 5000,
        "\n".join(f"line {index}" for index in range(1000)),
    ]
    for budget in range(101):
        for text in texts:
            out = ledger(default=budget).clip("read", text, hint="hint\n" * 1000)
            assert len(out) <= budget * CHARS_PER_TOKEN


def test_attached_context_obeys_every_tiny_budget():
    for budget in range(101):
        led = ledger(default=budget)
        current = led.clip("read", "result\n" * 1000)

        out = led.attach("read", current, "instructions\n" * 1000)

        assert len(out) <= budget * CHARS_PER_TOKEN
        assert led.tool_calls == 1
        assert led.tool_tokens == estimate_tokens(out)


def test_summary_does_not_allocate_the_withheld_output():
    led = ledger(default=1)
    led.clip("read", "x" * 10_000_000)
    assert "~2499999 tokens withheld" in led.summary()


class _CrashingTool:
    name = "crash"
    description = "Crash with adversarial output."
    parameters = {"type": "object"}

    def run(self, _args: dict, _ctx: ToolContext) -> ToolResult:
        raise RuntimeError("failure\n" * 10_000)


def test_registry_generated_errors_always_pass_through_ledger(tmp_path):
    led = ledger(default=12)
    context = ToolContext(tmp_path, led)
    registry = Registry().add(_CrashingTool())

    malformed = registry.dispatch("crash", "{", context)
    crashed = registry.dispatch("crash", "{}", context)
    unknown = registry.dispatch("missing", "{}", context)

    assert not malformed.ok and not crashed.ok and not unknown.ok
    assert len(crashed.output) <= 12 * CHARS_PER_TOKEN
    assert led.tool_calls == 3
    assert led.drops


def test_project_instruction_discovery_failure_is_a_bounded_tool_error(
    tmp_path, monkeypatch
):
    led = ledger(default=10)
    context = ToolContext(tmp_path, led)
    registry = Registry().add(_CrashingTool())

    def fail(_args):
        raise OSError("unreadable\n" * 10_000)

    monkeypatch.setattr(context, "discover_project_docs", fail)
    result = registry.dispatch("crash", "{}", context)

    assert not result.ok
    assert len(result.output) <= 10 * CHARS_PER_TOKEN
    assert led.tool_calls == 1
