"""The truncation policy. This is the file that decides what a run costs."""

from __future__ import annotations

from mjj.ledger import CHARS_PER_TOKEN, Budget, Ledger, estimate_tokens


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
