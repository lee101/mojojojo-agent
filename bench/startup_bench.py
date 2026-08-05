"""Reproducible cold-process and first-render latency measurements."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(arguments: list[str], *, input_text: str = "", env: dict[str, str]) -> float:
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-m", "mjj", *arguments],
        cwd=ROOT,
        env=env,
        input=input_text,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
        check=False,
    )
    elapsed = (time.perf_counter() - started) * 1000
    if completed.returncode:
        raise RuntimeError(
            f"startup case {arguments or ['interactive']} exited {completed.returncode}"
        )
    return elapsed


def measure(repeats: int = 9) -> dict[str, dict[str, float]]:
    cases = {
        "version": (["--version"], ""),
        "help": (["--help"], ""),
        "stdlib_ui_exit": ([], "/exit\n"),
    }
    results: dict[str, dict[str, float]] = {}
    with tempfile.TemporaryDirectory(prefix="mjj-startup-") as temporary:
        env = os.environ.copy()
        env.update(
            MJJ_HOME=temporary,
            MJJ_TUI="basic",
            PYTHONPATH=str(ROOT),
            PYTHONUTF8="1",
        )
        for name, (arguments, input_text) in cases.items():
            _run(arguments, input_text=input_text, env=env)
            samples = [
                _run(arguments, input_text=input_text, env=env)
                for _ in range(max(1, repeats))
            ]
            ordered = sorted(samples)
            results[name] = {
                "median_ms": round(statistics.median(samples), 3),
                "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
            }
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=9)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    results = measure(max(1, args.repeats))
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
        return 0
    print("| Startup case | median | p95 |")
    print("|---|---:|---:|")
    for name, values in results.items():
        print(f"| {name} | {values['median_ms']:.3f} ms | {values['p95_ms']:.3f} ms |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
