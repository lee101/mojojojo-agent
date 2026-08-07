"""Discover and run a project's self-test / check command."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .base import ToolContext, ToolResult

MAX_OUTPUT = 8_000
DEFAULT_TIMEOUT = 180
MAX_TIMEOUT = 600


class VerifyTool:
    name = "verify"
    requires_approval = True
    description = (
        "Run the workspace self-test (npm run check, pytest, make test, …). "
        "Prefer this after meaningful edits instead of inventing ad-hoc shell."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Optional override; otherwise auto-detect.",
            },
            "timeout": {
                "type": "integer",
                "minimum": 5,
                "maximum": MAX_TIMEOUT,
                "description": f"Seconds (default {DEFAULT_TIMEOUT}).",
            },
        },
        "additionalProperties": False,
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        timeout = args.get("timeout", DEFAULT_TIMEOUT)
        if not isinstance(timeout, int) or not (5 <= timeout <= MAX_TIMEOUT):
            return self._result(ctx, "timeout must be an integer 5..600", ok=False)
        override = args.get("command")
        if override is not None and (not isinstance(override, str) or not override.strip()):
            return self._result(ctx, "command must be a non-empty string", ok=False)
        try:
            label, argv, shell = (
                ("override", override.strip(), True)
                if override
                else discover_verify(ctx.cwd.resolve())
            )
        except ValueError as exc:
            return self._result(ctx, str(exc), ok=False)

        started = time.perf_counter()
        try:
            completed = subprocess.run(
                argv if not shell else override.strip(),
                cwd=ctx.cwd.resolve(),
                shell=shell,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired:
            return self._result(
                ctx,
                f"verify timed out after {timeout}s ({label})",
                ok=False,
                command=label,
            )
        except OSError as exc:
            return self._result(
                ctx, f"verify failed to start: {exc}", ok=False, command=label
            )

        ms = (time.perf_counter() - started) * 1000
        body = (completed.stdout or "").strip()
        if len(body) > MAX_OUTPUT:
            body = body[: MAX_OUTPUT // 2] + "\n…\n" + body[-MAX_OUTPUT // 2 :]
        ok = completed.returncode == 0
        status = "✓" if ok else "FAIL"
        header = f"verify {status} · {label} · exit {completed.returncode} · {ms:.0f} ms"
        text = header if not body else f"{header}\n{body}"
        return self._result(ctx, text, ok=ok, command=label, exit=completed.returncode)

    @staticmethod
    def _result(ctx: ToolContext, text: str, *, ok: bool = True, **meta) -> ToolResult:
        return ToolResult(
            ctx.ledger.clip("verify", text, hint="narrow the suite or raise timeout"),
            ok=ok,
            meta=meta,
        )


def discover_verify(root: Path) -> tuple[str, list[str], bool]:
    """Return (label, argv, shell). Prefer project-declared check scripts."""
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        scripts = data.get("scripts") if isinstance(data, dict) else None
        if isinstance(scripts, dict):
            for name in ("check", "test", "self-test", "selftest"):
                if isinstance(scripts.get(name), str) and scripts[name].strip():
                    npm = shutil.which("npm")
                    if not npm:
                        raise ValueError("package.json has scripts but npm is missing")
                    return f"npm run {name}", [npm, "run", name], False

    for candidate in (
        root / "scripts" / "self-test.sh",
        root / "scripts" / "check.sh",
        root / "self-test.sh",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.name, [str(candidate)], False

    makefile = root / "Makefile"
    if makefile.is_file():
        text = makefile.read_text(encoding="utf-8", errors="replace")
        for target in ("test", "check", "self-test"):
            if f"\n{target}:" in text or text.startswith(f"{target}:"):
                make = shutil.which("make")
                if make:
                    return f"make {target}", [make, target], False

    pyproject = root / "pyproject.toml"
    if pyproject.is_file() or (root / "pytest.ini").is_file() or (root / "tests").is_dir():
        pytest = _project_pytest(root) or shutil.which("pytest")
        if pytest:
            return "pytest", [pytest, "-q"], False

    raise ValueError(
        "no self-test found (looked for npm scripts check/test, Makefile, pytest, scripts/self-test.sh)"
    )


def _project_pytest(root: Path) -> str | None:
    for path in (
        root / ".venv" / "bin" / "pytest",
        root / ".venv" / "Scripts" / "pytest.exe",
        root / "venv" / "bin" / "pytest",
    ):
        if path.is_file():
            return str(path)
    return None


TOOLS = [VerifyTool()]
