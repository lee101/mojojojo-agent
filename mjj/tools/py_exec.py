"""The ``py`` tool: execute generated Python on the cheapest safe path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ..exec import ExecutionResult, execute
from .base import ToolContext, ToolResult


@dataclass
class PyTool:
    name: str = "py"
    requires_approval: ClassVar[bool] = True
    description: str = "Run Python; pure code is local/JIT, unsafe code is isolated."
    parameters: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.parameters = {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source"},
                "timeout": {
                    "type": "number",
                    "description": "Hard wall timeout in seconds (default 10)",
                },
                "packages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Packages for an isolated run",
                },
                "where": {
                    "type": "string",
                    "enum": ["auto", "inproc", "accel", "sandbox", "remote"],
                    "description": "Optional execution-path override",
                },
            },
            "required": ["code"],
            "additionalProperties": False,
        }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            return _error(ctx, "code must be a non-empty string")

        timeout = args.get("timeout", 10.0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            return _error(ctx, "timeout must be a number of seconds")
        timeout = float(timeout)
        if not 0.01 <= timeout <= 300.0:
            return _error(ctx, "timeout must be between 0.01 and 300 seconds")

        packages = args.get("packages")
        if packages is not None and (
            not isinstance(packages, list)
            or any(not isinstance(item, str) or not item.strip() for item in packages)
        ):
            return _error(ctx, "packages must be a list of non-empty strings")
        packages = list(packages or [])

        where = args.get("where")
        if where is not None and not isinstance(where, str):
            return _error(ctx, "where must be a string")
        try:
            result = execute(
                code,
                timeout=timeout,
                packages=packages,
                where=where,
                cwd=ctx.cwd,
            )
        except ValueError as exc:
            return _error(ctx, str(exc))
        except Exception as exc:
            # An executor bug remains a tool result, consistent with Registry,
            # and still consumes the py budget rather than bypassing it.
            text = ctx.ledger.clip(
                "py", f"py executor failed: {type(exc).__name__}: {exc}"
            )
            return ToolResult.error(text)

        rendered = _render(result)
        clipped = ctx.ledger.clip(
            "py",
            rendered,
            hint="rerun with less program output",
        )
        return ToolResult(output=clipped, ok=result.ok, meta=result.metadata())


def _error(ctx: ToolContext, message: str) -> ToolResult:
    return ToolResult.error(ctx.ledger.clip("py", message))


def _render(result: ExecutionResult) -> str:
    bits = [
        f"path={result.path}",
        f"tier={result.tier}",
        f"exit={result.exit_code}",
        f"wall_ms={result.wall_ms:.3f}",
    ]
    if result.timed_out:
        bits.append("timed_out=true")
    if result.path == "remote" or result.credit_cost:
        bits.append(f"credit_cost={result.credit_cost:g}")
    if result.requested_path:
        bits.append(f"requested={result.requested_path}")
    lines = [" ".join(bits)]
    if result.fallback:
        lines.append(f"fallback: {result.fallback}")
    lines.extend(["stdout:", result.stdout.rstrip("\n")])
    # stderr is deliberately last: ledger clipping preserves the tail, so a
    # traceback survives even after a very noisy stdout.
    lines.extend(["stderr:", result.stderr.rstrip("\n")])
    return "\n".join(lines).rstrip() + "\n"


TOOLS = [PyTool()]

__all__ = ["PyTool", "TOOLS"]
