"""Drive `mjj` over a list of Python packages to port to Mojo.

This is the harness eating its own cooking. mojo-factory
(github.com/lee101/mojo-factory) does the same job with the codex CLI; this
does it with mjj, which is the point — a porting run is a long, tool-heavy,
token-hungry workload, and it is the honest way to find out whether the
efficiency claims in this repo survive contact with real work.

    python scripts/port.py --targets targets.tsv --workers 3
    python scripts/port.py --slug mojo-acora --package acora --scope "..."

Each target gets three passes in its own directory: build, then accelerate,
then review, each gated on `pixi run build && pixi run test`. A pass that
cannot make the gate green leaves the tree alone and records why. Nothing is
committed or pushed from here — publishing stays a human decision.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mjj.agent import Agent  # noqa: E402
from mjj.ledger import Ledger  # noqa: E402
from mjj.model import ModelClient  # noqa: E402
from mjj.session import Session  # noqa: E402
from mjj.tools import build_registry  # noqa: E402

NOTES = Path("/nvme0n1-disk/code/mojo-factory/MOJO_NOTES.md")

BUILD = """Port the Python package `{package}` to Mojo in this directory.

Scope: {scope}

Read MOJO_NOTES.md first — it holds the Mojo 1.0 dialect and FFI facts that
save the most time here. Then:
- implement the scope in `src/`, idiomatic Mojo, not a transliteration
- write `build/build.sh`; `pixi.toml` already calls it as the `build` task
- expose the library through a C ABI and a thin `python/` wrapper, so the
  tests (pytest) can exercise it and so callers are not forced into Mojo
- write real tests under `tests/` that would fail if the logic were wrong
- `pixi run build && pixi run test` must be green when you finish

Do not commit, do not push, do not touch anything outside this directory."""

ACCEL = """Make this Mojo package fast: SIMD where the data is contiguous,
`parallelize` where the work is independent, and GPU only where arithmetic
intensity justifies the transfer (see MOJO_NOTES.md).

Measure before and after with the `bench` task. Keep `pixi run build && pixi
run test` green. Publish honest numbers — a case where Mojo is slower than the
Python original gets reported as slower, never quietly dropped."""

REVIEW = """Review this package as a hostile reader. Look for wrong results,
unchecked bounds, borrowed pointers that outlive their owner, and benchmarks
that measure nothing. Fix what you find. Update README.md so its claims match
what the code and the benchmark actually do. Keep the gate green."""


@dataclass
class PortResult:
    slug: str
    ok: bool
    stage: str
    seconds: float
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    tool_calls: int
    detail: str = ""


def gate(work: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        ["pixi", "run", "build"], cwd=work, capture_output=True, text=True, timeout=1800
    )
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout)[-400:]
    proc = subprocess.run(
        ["pixi", "run", "test"], cwd=work, capture_output=True, text=True, timeout=1800
    )
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout)[-400:]
    return True, ""


def run_pass(work: Path, prompt: str, model: str, effort: str) -> tuple[Ledger, ModelClient, str]:
    client = ModelClient(model=model, effort=effort)
    ledger = Ledger()
    agent = Agent(
        registry=build_registry(),
        client=client,
        cwd=work,
        ledger=ledger,
        session=Session(meta={"port": work.name}),
    )
    error = ""
    for step in agent.run(prompt):
        if step.kind == "error":
            error = step.text[:200]
    return ledger, client, error


def port(slug: str, package: str, scope: str, root: Path, model: str, effort: str) -> PortResult:
    work = root / slug
    work.mkdir(parents=True, exist_ok=True)
    if NOTES.is_file() and not (work / "MOJO_NOTES.md").exists():
        (work / "MOJO_NOTES.md").write_text(NOTES.read_text())
    started = time.monotonic()
    totals = {"in": 0, "cached": 0, "out": 0, "tools": 0}
    for stage, prompt in (
        ("build", BUILD.format(package=package, scope=scope)),
        ("accel", ACCEL),
        ("review", REVIEW),
    ):
        ledger, client, error = run_pass(work, prompt, model, effort)
        totals["in"] += client.usage.input_tokens
        totals["cached"] += client.usage.cached_input_tokens
        totals["out"] += client.usage.output_tokens
        totals["tools"] += ledger.tool_calls
        green, why = gate(work)
        if not green:
            return PortResult(
                slug, False, stage, time.monotonic() - started,
                totals["in"], totals["cached"], totals["out"], totals["tools"],
                detail=(error or why).strip().splitlines()[-1][:160] if (error or why) else "gate failed",
            )
    return PortResult(
        slug, True, "review", time.monotonic() - started,
        totals["in"], totals["cached"], totals["out"], totals["tools"],
    )


def load_targets(path: Path, limit: int) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    with path.open() as handle:
        for row in csv.reader(handle, delimiter="\t"):
            if len(row) >= 3 and not row[0].startswith("#"):
                rows.append((row[0], row[1], row[2]))
            if len(rows) >= limit:
                break
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", type=Path)
    parser.add_argument("--slug")
    parser.add_argument("--package")
    parser.add_argument("--scope", default="the core computational surface")
    parser.add_argument("--root", type=Path, default=Path("/nvme0n1-disk/code"))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--model", default=ModelClient.model)
    parser.add_argument("--effort", default="high")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    if args.slug:
        targets = [(args.slug, args.package or args.slug.removeprefix("mojo-"), args.scope)]
    elif args.targets:
        targets = load_targets(args.targets, args.limit)
    else:
        parser.error("pass --slug or --targets")

    def one(target):
        slug, package, scope = target
        try:
            return port(slug, package, scope, args.root, args.model, args.effort)
        except Exception as exc:
            return PortResult(slug, False, "driver", 0.0, 0, 0, 0, 0, f"{type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = list(pool.map(one, targets))

    for result in results:
        mark = "pass" if result.ok else f"FAIL({result.stage})"
        print(
            f"{mark:14} {result.slug:28} {result.seconds:7.1f}s  "
            f"in {result.input_tokens:>8} ({result.cached_tokens:>8} cached)  "
            f"out {result.output_tokens:>6}  tools {result.tool_calls:>3}  {result.detail}"
        )
    if args.json_path:
        Path(args.json_path).write_text(json.dumps([asdict(r) for r in results], indent=2))
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
