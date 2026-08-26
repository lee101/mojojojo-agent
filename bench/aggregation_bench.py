"""Token cost of tool-output clipping: positional head/tail vs aggregation.

Reproduces the numbers quoted for the ledger's signal-aware aggregator. Every
corpus is a realistic oversized tool dump; the baseline is the previous
positional policy (55% head / 45% tail). "signal kept" checks that a decisive
diagnostic placed far from both ends survives.

    uv run python bench/aggregation_bench.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mjj.ledger import CHARS_PER_TOKEN, Budget, Ledger

BUDGET_TOKENS = 400


def _positional_clip(text: str, limit: int) -> str:
    """The previous policy, verbatim in spirit: head 55%, tail 45%."""
    lines = text.splitlines()
    content_room = max(0, limit - 106 - 2)
    head_chars = int(content_room * 0.55)
    head, size = [], 0
    for line in lines:
        if size + len(line) + 1 > head_chars:
            break
        head.append(line)
        size += len(line) + 1
    tail, size = [], 0
    for line in reversed(lines):
        if size + len(line) + 1 > content_room - head_chars:
            break
        tail.append(line)
        size += len(line) + 1
    marker = f"… {len(lines) - len(head) - len(tail)} lines omitted …"
    return "\n".join([*head, marker, *reversed(tail)])


def _build_log() -> tuple[str, str]:
    lines = [f"[{i % 12}:{i * 7 % 60:02d}] compiling src/module_{i}.rs" for i in range(6000)]
    lines[3000] = "error[E0308]: mismatched types in src/pay.rs:412"
    lines += ["warning: unused import"] * 2500
    return "\n".join(lines), "mismatched types"


def _test_log() -> tuple[str, str]:
    lines = [f"test_case_{i} ... ok" for i in range(5000)]
    lines[4200] = "FAILED tests/test_payments.py::test_refund_flow"
    lines[4201] = "E       assert 0 == 1"
    return "\n".join(lines), "test_refund_flow"


def _grep_flood() -> tuple[str, str]:
    lines = [f"vendor/pkg-{i % 40}/dist/index.js:{i}: export const x{i}" for i in range(8000)]
    lines[6000] = "web/src/App.tsx:118: onNavigate('/checkout')"
    return "\n".join(lines), "App.tsx:118"


def _retry_spam() -> tuple[str, str]:
    lines = []
    for i in range(40_000):
        lines.append("retrying deployment (attempt 37) · backing off 500ms")
        if i % 8000 == 0:
            lines.append(f"deploy step {i // 8000} done")
    lines.append("deployment failed: quota exceeded")
    return "\n".join(lines), "quota exceeded"


def main() -> None:
    corpora = [
        ("build log + repeated warnings", *_build_log()),
        ("pytest failure mid-run", *_test_log()),
        ("grep flood with one real hit", *_grep_flood()),
        ("retry spam until failure", *_retry_spam()),
    ]
    limit = BUDGET_TOKENS * CHARS_PER_TOKEN
    print(f"budget {BUDGET_TOKENS} tokens ({limit} chars)")
    print(
        f"{'corpus':34} {'baseline':>9} {'aggregate':>10} "
        f"{'saved':>7} {'base kept':>9} {'agg kept':>8} {'clip ms':>8}"
    )
    failures = 0
    for name, text, signal in corpora:
        baseline = _positional_clip(text, limit)
        led = Ledger(budget=Budget(default=BUDGET_TOKENS))
        started = time.perf_counter()
        aggregated = led.clip("shell", text)
        seconds = (time.perf_counter() - started) * 1000
        base_tokens = estimate(baseline)
        agg_tokens = estimate(aggregated)
        saved = 100 * (1 - agg_tokens / base_tokens)
        base_kept = signal in baseline
        agg_kept = signal in aggregated
        failures += not agg_kept
        print(
            f"{name:34} {base_tokens:>7}t {agg_tokens:>8}t "
            f"{saved:>6.1f}% {base_kept!s:>9} {agg_kept!s:>8} {seconds:>7.1f}"
        )
    if failures:
        raise SystemExit(f"{failures} corpora lost their diagnostic signal")


def estimate(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


if __name__ == "__main__":
    main()
