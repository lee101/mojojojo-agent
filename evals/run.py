"""End-to-end evals: can the agent actually build and fix real things?

Each eval is a directory under ``evals/cases/<name>/`` containing:

    setup.sh    optional, run once in a scratch copy before the agent starts
    prompt.txt  what the agent is told
    check.sh    exit 0 if the agent succeeded

The agent runs in a throwaway copy, so a case can be as destructive as it
likes. What we record per case is not just pass/fail but **what it cost**:
tokens in and out, tool calls, wall clock. A change that makes the agent
smarter and doubles the token bill is not obviously an improvement, and this
is the file that makes that visible.

    python evals/run.py                 # every case
    python evals/run.py port-to-mojo    # one case
    python evals/run.py --json out.json # machine-readable, for tracking drift
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mjj.agent import Agent  # noqa: E402
from mjj.ledger import Ledger  # noqa: E402
from mjj.model import ModelClient  # noqa: E402
from mjj.tools import build_registry  # noqa: E402

CASES = Path(__file__).parent / "cases"


@dataclass
class Result:
    case: str
    passed: bool
    seconds: float
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    tool_calls: int
    tool_tokens: int
    detail: str = ""

    def line(self) -> str:
        mark = "pass" if self.passed else "FAIL"
        return (
            f"{mark:4} {self.case:24} {self.seconds:6.1f}s  "
            f"in {self.input_tokens:>7} ({self.cached_tokens:>7} cached)  "
            f"out {self.output_tokens:>6}  tools {self.tool_calls:>3}"
            + (f"  {self.detail}" if self.detail else "")
        )


def run_case(case: Path, model: str, effort: str) -> Result:
    prompt = (case / "prompt.txt").read_text().strip()
    with tempfile.TemporaryDirectory(prefix="mjj-eval-") as tmp:
        work = Path(tmp) / case.name
        shutil.copytree(case / "repo", work) if (case / "repo").is_dir() else work.mkdir(parents=True)
        setup = case / "setup.sh"
        if setup.is_file():
            subprocess.run(["bash", str(setup)], cwd=work, check=True)

        client = ModelClient(model=model, effort=effort)
        ledger = Ledger()
        agent = Agent(
            registry=build_registry(), client=client, cwd=work, ledger=ledger
        )
        started = time.monotonic()
        detail = ""
        for step in agent.run(prompt):
            if step.kind == "error":
                detail = step.text[:120]
        elapsed = time.monotonic() - started

        check = subprocess.run(
            ["bash", str(case / "check.sh")], cwd=work, capture_output=True, text=True
        )
        passed = check.returncode == 0
        if not passed and not detail:
            detail = (check.stdout + check.stderr).strip().splitlines()[-1:][0][:120] if (check.stdout or check.stderr) else "check failed"
        usage = client.usage
        return Result(
            case=case.name,
            passed=passed,
            seconds=elapsed,
            input_tokens=usage.input_tokens,
            cached_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            tool_calls=ledger.tool_calls,
            tool_tokens=ledger.tool_tokens,
            detail=detail,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", nargs="*", help="case names; default is all")
    parser.add_argument("--model", default=ModelClient.model)
    parser.add_argument("--effort", default="high")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    selected = sorted(
        p for p in CASES.iterdir()
        if p.is_dir() and (not args.cases or p.name in args.cases)
    ) if CASES.is_dir() else []
    if not selected:
        print("no cases found", file=sys.stderr)
        return 2

    results = [run_case(case, args.model, args.effort) for case in selected]
    for result in results:
        print(result.line())
    failed = sum(1 for r in results if not r.passed)
    total_in = sum(r.input_tokens for r in results)
    total_out = sum(r.output_tokens for r in results)
    print(f"\n{len(results) - failed}/{len(results)} passed · "
          f"{total_in} input · {total_out} output tokens")
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps([asdict(r) for r in results], indent=2)
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
