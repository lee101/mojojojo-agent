"""Tool assembly.

Each module exposes ``TOOLS`` — a list of tool instances. Assembly is tolerant:
a module that fails to import (missing optional backend, half-finished branch)
costs its own tools, not the whole harness.
"""

from __future__ import annotations

import os

from .base import Registry, Tool, ToolContext, ToolResult  # noqa: F401

MODULES = ("fs", "shell", "patch", "search", "py_exec")


def build_registry(only: list[str] | None = None) -> Registry:
    registry = Registry()
    disabled = {n for n in (os.environ.get("MJJ_DISABLE_TOOLS") or "").split(",") if n}
    for module_name in MODULES:
        if only and module_name not in only:
            continue
        try:
            module = __import__(f"mjj.tools.{module_name}", fromlist=["TOOLS"])
        except Exception:
            continue
        for tool in getattr(module, "TOOLS", []):
            if tool.name not in disabled:
                registry.add(tool)
    return registry
