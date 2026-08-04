"""Measure native visualizer expansion speed and harness-context token cost."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from mjj.ledger import Budget, Ledger, estimate_tokens  # noqa: E402
from mjj.skills import BUILTIN_DIR  # noqa: E402
from mjj.tools.base import ToolContext  # noqa: E402
from mjj.tools.skills import SkillTool  # noqa: E402
from mjj.visualize import KINDS, generate_visualizer  # noqa: E402


BUDGETS = (16, 24, 32, 48, 64, 96, 128)


def benchmark(iterations: int = 20) -> dict:
    if iterations < 1:
        raise ValueError("iterations must be positive")
    timings: list[float] = []
    summaries: list[str] = []
    source_tokens: list[int] = []
    with tempfile.TemporaryDirectory(prefix="mjj-visualbench-") as temporary:
        workspace = Path(temporary)
        for index in range(iterations):
            kind = KINDS[index % len(KINDS)]
            result = generate_visualizer(
                "field",
                cwd=workspace,
                kind=kind,
                palette=("ultraviolet", "pelagic", "ember", "acid")[index % 4],
                seed=29 + index,
                title="Measured signal field",
                force=True,
            )
            timings.append(result.milliseconds)
            summaries.append(result.summary())
            source_tokens.append(result.source_tokens)

        skill_result = SkillTool(include_user=False).run(
            {"name": "visualizer"}, ToolContext(workspace, Ledger())
        )

    representative = max(summaries, key=len) + "\nexit code: 0"
    shell_arguments = json.dumps(
        {
            "command": [
                "mjj",
                "visualize",
                "field",
                "--kind",
                "aurora",
                "--palette",
                "ultraviolet",
                "--seed",
                "29",
                "--title",
                "Measured signal field",
            ]
        },
        separators=(",", ":"),
    )
    budget_sweep = []
    for budget in BUDGETS:
        ledger = Ledger(Budget(default=budget))
        clipped = ledger.clip("shell", representative)
        budget_sweep.append(
            {
                "budget_tokens": budget,
                "result_tokens": estimate_tokens(clipped),
                "lossless": clipped == representative,
            }
        )
    minimum_lossless = next(
        item["budget_tokens"] for item in budget_sweep if item["lossless"]
    )
    shell_context_tokens = estimate_tokens(shell_arguments) + estimate_tokens(representative)
    skill_tokens = estimate_tokens(skill_result.output)
    raw_tokens = round(statistics.fmean(source_tokens))
    first_use = shell_context_tokens + skill_tokens
    return {
        "iterations": iterations,
        "generation_milliseconds": {
            "median": round(statistics.median(timings), 3),
            "p95": round(_percentile(timings, 0.95), 3),
            "maximum": round(max(timings), 3),
        },
        "tokens": {
            "generated_source": raw_tokens,
            "skill_instructions": skill_tokens,
            "shell_call_and_result": shell_context_tokens,
            "first_use_total": first_use,
            "repeat_use_total": shell_context_tokens,
            "first_use_amplification": round(raw_tokens / max(first_use, 1), 2),
            "repeat_use_amplification": round(raw_tokens / max(shell_context_tokens, 1), 2),
            "minimum_lossless_result_budget": minimum_lossless,
            "always_on_schema_tax": 0,
        },
        "budget_sweep": budget_sweep,
        "skill_path": str(BUILTIN_DIR / "visualizer" / "SKILL.md"),
    }


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()
    try:
        report = benchmark(args.iterations)
    except ValueError as exc:
        parser.error(str(exc))
    timing = report["generation_milliseconds"]
    tokens = report["tokens"]
    print(
        f"native generation median {timing['median']:.3f} ms · p95 {timing['p95']:.3f} ms"
    )
    print(
        f"source ~{tokens['generated_source']} tokens · first use {tokens['first_use_total']} · "
        f"repeat {tokens['repeat_use_total']} · amplification "
        f"{tokens['first_use_amplification']:.1f}x/{tokens['repeat_use_amplification']:.1f}x"
    )
    print(
        f"minimum lossless shell-result budget {tokens['minimum_lossless_result_budget']} tokens · "
        "always-on schema tax 0"
    )
    destination = Path(args.json_path) if args.json_path else ROOT / "output" / "budget.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
