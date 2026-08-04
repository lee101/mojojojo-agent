"""Opt-in installed plugin tools with a bounded, failure-isolated boundary.

Plugins are Python package entry points in the ``mojojojo.tools`` group.  MJJ
never imports them merely because they are installed: callers must explicitly
enable an entry-point name through trusted configuration or ``MJJ_PLUGINS``.
Every contributed tool is namespaced and its result is clipped by the ledger,
even when the plugin forgets to do so itself.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import metadata
from itertools import islice
from typing import Any, Iterable

from .tools.base import ToolContext, ToolResult


ENTRY_POINT_GROUP = "mojojojo.tools"
MAX_PLUGINS = 8
MAX_TOOLS_PER_PLUGIN = 16
MAX_TOOL_SCHEMA_BYTES = 16 * 1024
MAX_TOTAL_SCHEMA_BYTES = 64 * 1024
MAX_DESCRIPTION_CHARS = 512
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class PluginInfo:
    name: str
    value: str
    distribution: str = ""

    def public(self, *, enabled: bool = False) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "distribution": self.distribution or None,
            "enabled": enabled,
        }


@dataclass
class _PluginTool:
    plugin_name: str
    inner: Any
    name: str
    description: str
    parameters: dict
    schema_bytes: int

    @property
    def requires_approval(self) -> bool:
        return bool(getattr(self.inner, "requires_approval", False))

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        result = self.inner.run(args, ctx)
        if not isinstance(result, ToolResult):
            return ToolResult.error(
                ctx.ledger.clip(
                    self.name,
                    f"plugin {self.plugin_name!r} returned "
                    f"{type(result).__name__}, expected ToolResult",
                ),
                plugin=self.plugin_name,
            )
        result.output = ctx.ledger.clip(self.name, str(result.output))
        result.meta.setdefault("plugin", self.plugin_name)
        return result


def plugin_inventory() -> tuple[PluginInfo, ...]:
    """List installed plugin entry points without importing plugin modules."""
    return tuple(
        PluginInfo(
            name=point.name,
            value=point.value,
            distribution=_distribution_name(point),
        )
        for point in _entry_points()
    )


def load_plugin_tools(
    enabled: Iterable[str],
) -> tuple[list[_PluginTool], list[str], list[Any]]:
    """Load explicitly enabled plugins and return tools, warnings, resources."""
    requested = tuple(
        dict.fromkeys(str(name).strip() for name in enabled if str(name).strip())
    )
    if len(requested) > MAX_PLUGINS:
        return [], [f"plugins: at most {MAX_PLUGINS} plugins may be enabled"], []

    available: dict[str, list[Any]] = {}
    for point in _entry_points():
        available.setdefault(point.name, []).append(point)

    tools: list[_PluginTool] = []
    warnings: list[str] = []
    resources: list[Any] = []
    total_schema_bytes = 0
    for name in requested:
        if not _NAME.fullmatch(name):
            warnings.append(_warning(name, "invalid plugin name"))
            continue
        candidates = available.get(name, [])
        if not candidates:
            warnings.append(_warning(name, "installed entry point was not found"))
            continue
        if len(candidates) > 1:
            warnings.append(_warning(name, "multiple entry points found; using the first"))
        point = candidates[0]
        try:
            exported = point.load()
            plugin = exported() if callable(exported) and not _is_tool(exported) else exported
            if callable(getattr(plugin, "close", None)):
                resources.append(plugin)
            raw_tools = _plugin_tools(plugin)
        except Exception as exc:
            warnings.append(_warning(name, f"load failed: {type(exc).__name__}: {exc}"))
            continue
        if len(raw_tools) > MAX_TOOLS_PER_PLUGIN:
            warnings.append(
                _warning(name, f"exposes more than {MAX_TOOLS_PER_PLUGIN} tools")
            )
            continue
        seen: set[str] = set()
        for raw_tool in raw_tools:
            try:
                wrapped = _wrap_tool(name, raw_tool)
            except (TypeError, ValueError) as exc:
                warnings.append(_warning(name, str(exc)))
                continue
            if wrapped.name in seen:
                warnings.append(_warning(name, f"duplicate tool {wrapped.name!r} ignored"))
                continue
            if total_schema_bytes + wrapped.schema_bytes > MAX_TOTAL_SCHEMA_BYTES:
                warnings.append(
                    _warning(
                        name,
                        f"tool schema budget exceeds {MAX_TOTAL_SCHEMA_BYTES} bytes; "
                        f"{wrapped.name!r} ignored",
                    )
                )
                continue
            seen.add(wrapped.name)
            tools.append(wrapped)
            total_schema_bytes += wrapped.schema_bytes
    return tools, warnings, resources


def _entry_points() -> list[Any]:
    points = metadata.entry_points()
    selected = (
        points.select(group=ENTRY_POINT_GROUP)
        if hasattr(points, "select")
        else points.get(ENTRY_POINT_GROUP, ())
    )
    return sorted(selected, key=lambda point: (point.name, point.value))


def _distribution_name(point: Any) -> str:
    distribution = getattr(point, "dist", None)
    if distribution is None:
        return ""
    name = getattr(distribution, "name", None)
    if isinstance(name, str):
        return name
    try:
        return str(distribution.metadata.get("Name", ""))
    except Exception:
        return ""


def _plugin_tools(plugin: Any) -> list[Any]:
    if _is_tool(plugin):
        return [plugin]
    raw = getattr(plugin, "tools", plugin)
    if isinstance(raw, (str, bytes, dict)):
        raise TypeError("plugin must return a tool, iterable of tools, or object with .tools")
    try:
        return list(islice(iter(raw), MAX_TOOLS_PER_PLUGIN + 1))
    except TypeError as exc:
        raise TypeError(
            "plugin must return a tool, iterable of tools, or object with .tools"
        ) from exc


def _is_tool(value: Any) -> bool:
    return all(hasattr(value, field) for field in ("name", "description", "parameters", "run"))


def _wrap_tool(plugin_name: str, tool: Any) -> _PluginTool:
    if not _is_tool(tool) or not callable(getattr(tool, "run", None)):
        raise TypeError("plugin item does not implement the Tool contract")
    local_name = getattr(tool, "name")
    if not isinstance(local_name, str) or not _TOOL_NAME.fullmatch(local_name):
        raise ValueError(f"invalid tool name {local_name!r}")
    qualified = f"{plugin_name}__{local_name}"
    if len(qualified) > 64:
        raise ValueError(f"qualified tool name {qualified!r} exceeds 64 characters")
    description = getattr(tool, "description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"tool {local_name!r} needs a description")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise ValueError(
            f"tool {local_name!r} description exceeds {MAX_DESCRIPTION_CHARS} characters"
        )
    parameters = getattr(tool, "parameters")
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        raise ValueError(f"tool {local_name!r} parameters must be an object schema")
    try:
        encoded = json.dumps(parameters, separators=(",", ":"), ensure_ascii=False).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"tool {local_name!r} parameters are not JSON serializable") from exc
    if len(encoded) > MAX_TOOL_SCHEMA_BYTES:
        raise ValueError(
            f"tool {local_name!r} schema exceeds {MAX_TOOL_SCHEMA_BYTES} bytes"
        )
    return _PluginTool(
        plugin_name=plugin_name,
        inner=tool,
        name=qualified,
        description=description.strip(),
        parameters=parameters,
        schema_bytes=(
            len(encoded)
            + len(qualified.encode("utf-8"))
            + len(description.encode("utf-8"))
        ),
    )


def _warning(plugin_name: str, message: str) -> str:
    flattened = " ".join(str(message).split())
    if len(flattened) > 300:
        flattened = flattened[:299] + "…"
    return f"plugin {plugin_name}: {flattened}"
