"""Harness self-test — fast, offline suite the agent can run on itself."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# Keep this list tight: no network, no long evals, no visualbench.
CORE_TESTS = (
    "tests/test_plan.py",
    "tests/test_goals.py",
    "tests/test_check.py",
    "tests/test_verify.py",
    "tests/test_subagent_plan.py",
    "tests/test_cli.py",
    "tests/test_config.py",
    "tests/test_prompt.py",
    "tests/test_permissions.py",
    "tests/test_patch.py",
    "tests/test_fs.py",
)


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run_self_test(*, verbose: bool = False) -> tuple[int, str]:
    root = package_root()
    missing = [name for name in CORE_TESTS if not (root / name).is_file()]
    if missing:
        return 1, "missing tests:\n" + "\n".join(missing)

    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "-q" if not verbose else "-vv",
        *CORE_TESTS,
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        argv,
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    ms = (time.perf_counter() - started) * 1000
    body = (completed.stdout or "").strip()
    header = (
        f"mjj self-test {'ok' if completed.returncode == 0 else 'FAIL'} · "
        f"{len(CORE_TESTS)} files · {ms:.0f} ms · exit {completed.returncode}"
    )
    return completed.returncode, header + (("\n" + body) if body else "")
