"""Tool assembly.

Each module exposes ``TOOLS`` — a list of tool instances. Assembly is tolerant:
a module that fails to import (missing optional backend, half-finished branch)
costs its own tools, not the whole harness.
"""

from __future__ import annotations

import os

from .base import Registry, Tool, ToolContext, ToolResult  # noqa: F401

MODULES = (
    "fs",
    "shell",
    "delegate",
    "patch",
    "checkpoint",
    "search",
    "navigate",
    "check",
    "verify",
    "display",
    "read_image",
    "py_exec",
    "skills",
    "plan",
)


def build_registry(
    only: list[str] | None = None,
    *,
    disabled: tuple[str, ...] | list[str] = (),
    include_user_skills: bool = True,
    skill_paths=(),
    mcp_servers=(),
    plugins=(),
) -> Registry:
    registry = Registry()
    disabled_names = {
        n for n in (os.environ.get("MJJ_DISABLE_TOOLS") or "").split(",") if n
    }
    disabled_names.update(disabled)
    for module_name in MODULES:
        if only and module_name not in only:
            continue
        try:
            module = __import__(f"mjj.tools.{module_name}", fromlist=["TOOLS"])
        except Exception:
            continue
        tools = (
            module.build_tools(
                include_user=include_user_skills,
                extra_paths=skill_paths,
            )
            if module_name == "skills"
            else getattr(module, "TOOLS", [])
        )
        for tool in tools:
            if tool.name not in disabled_names:
                registry.add(tool)
    if (only is None or "mcp" in only) and mcp_servers:
        try:
            from ..mcp import discover_mcp_tools

            tools, warnings, clients = discover_mcp_tools(mcp_servers)
            registry.warnings.extend(warnings)
            registry.resources.extend(clients)
            for tool in tools:
                if tool.name not in disabled_names:
                    registry.add(tool)
        except Exception as exc:
            registry.warnings.append(f"MCP discovery failed: {exc}")
    if (only is None or "plugins" in only) and plugins:
        try:
            from ..plugins import load_plugin_tools

            tools, warnings, resources = load_plugin_tools(plugins)
            registry.warnings.extend(warnings)
            registry.resources.extend(resources)
            for tool in tools:
                if tool.name in disabled_names:
                    continue
                if tool.name in registry.tools:
                    registry.warnings.append(
                        f"plugin tool name collision ignored: {tool.name}"
                    )
                    continue
                registry.add(tool)
        except Exception as exc:
            registry.warnings.append(f"plugin discovery failed: {exc}")
    return registry
