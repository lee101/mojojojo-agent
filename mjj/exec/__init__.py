"""Execution backends shared by the :mod:`mjj.tools.py_exec` tool."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ExecutionResult:
    """The stable result shape returned by every execution path."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    wall_ms: float = 0.0
    path: str = "inproc"
    tier: str = "interpreted"
    timed_out: bool = False
    native: bool = False
    credit_cost: float = 0.0
    requested_path: str | None = None
    fallback: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def metadata(self) -> dict[str, Any]:
        """Small UI/session metadata; program output deliberately stays out."""
        out = {
            "path": self.path,
            "tier": self.tier,
            "native": self.native,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "wall_ms": self.wall_ms,
            "credit_cost": self.credit_cost,
        }
        if self.requested_path:
            out["requested_path"] = self.requested_path
        if self.fallback:
            out["fallback"] = self.fallback
        out.update(self.detail)
        return out


def execute(
    code: str,
    *,
    timeout: float = 10.0,
    packages: list[str] | None = None,
    where: str | None = None,
    cwd: Path | None = None,
) -> ExecutionResult:
    """Choose a path and execute ``code``.

    This convenience API mirrors the ``py`` tool without depending on its tool
    context. Backend failures degrade to the current interpreter.
    """
    from .local import run_accelerated, run_inproc, run_sandbox
    from .policy import choose_path
    from .remote import run_remote

    selected = choose_path(code, packages=packages, where=where)
    workdir = Path.cwd() if cwd is None else Path(cwd)
    if selected == "inproc":
        return run_inproc(code, timeout=timeout, cwd=workdir)
    if selected == "accelerated":
        return run_accelerated(code, timeout=timeout, cwd=workdir)
    if selected == "remote":
        return run_remote(
            code, timeout=timeout, packages=packages, fallback_cwd=workdir
        )
    return run_sandbox(
        code, timeout=timeout, packages=packages, fallback_cwd=workdir
    )


__all__ = ["ExecutionResult", "execute"]
