"""Agent Plugins Specification v1.0.0 loader (skills + stdio MCP).

Discovers ``plugin.json`` packages from project and user plugin roots, then
exposes their ``skills/`` directories and ``mcp.json`` stdio servers. Broken
plugins degrade to warnings; the coding harness still starts.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from .config import MCPServerConfig


PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
MAX_PLUGINS = 32
PLUGIN_NAME = re.compile(
    r"^(?!.*(?:--|\\.\\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$"
)
PROJECT_PLUGIN_DIRS = (
    ".agents/plugins",
    ".mjj/plugins",
    ".codex/plugins",
)
USER_PLUGIN_DIRS = (
    "~/.agents/plugins",
    "~/.mjj/plugins",
    "~/.codex/plugins",
    "~/.appnz/plugins",
)


@dataclass(frozen=True)
class AgentPlugin:
    name: str
    root: Path
    version: str = ""
    description: str = ""
    skill_dirs: tuple[Path, ...] = ()
    mcp_servers: tuple[MCPServerConfig, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass
class PluginBundle:
    plugins: tuple[AgentPlugin, ...] = ()
    skill_dirs: tuple[Path, ...] = ()
    mcp_servers: tuple[MCPServerConfig, ...] = ()
    warnings: list[str] = field(default_factory=list)


def discover(
    cwd: str | Path,
    *,
    include_user: bool = True,
    environ: Mapping[str, str] | None = None,
    extra_roots: Iterable[str | Path] = (),
) -> PluginBundle:
    """Load Agent Plugins packages from project, user, and explicit roots."""
    env = os.environ if environ is None else environ
    working = Path(cwd).expanduser().resolve()
    project = _project_root(working)
    home = Path(env.get("HOME") or env.get("USERPROFILE") or Path.home())
    mjj_home = Path(env.get("MJJ_HOME") or home / ".mjj").expanduser()
    data_root = mjj_home / "plugin-data"

    roots: list[Path] = []
    roots.extend(Path(path).expanduser().resolve() for path in extra_roots)
    for relative in PROJECT_PLUGIN_DIRS:
        roots.append((project / relative).resolve())
    if include_user:
        roots.append((mjj_home / "plugins").resolve())
        for raw in USER_PLUGIN_DIRS:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = home / path
            roots.append(path.resolve())

    bundle = PluginBundle()
    seen_roots: set[Path] = set()
    seen_names: set[str] = set()
    for root in roots:
        if root in seen_roots or not root.is_dir() or root.is_symlink():
            continue
        seen_roots.add(root)
        for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir() or child.is_symlink():
                continue
            if (child / "plugin.json").is_file():
                plugin = _load_plugin(child, data_root)
            else:
                continue
            if plugin is None:
                continue
            if plugin.name in seen_names:
                bundle.warnings.append(
                    f"agent-plugin {plugin.name}: duplicate ignored ({child})"
                )
                continue
            seen_names.add(plugin.name)
            bundle.plugins = (*bundle.plugins, plugin)
            bundle.skill_dirs = (*bundle.skill_dirs, *plugin.skill_dirs)
            bundle.mcp_servers = (*bundle.mcp_servers, *plugin.mcp_servers)
            bundle.warnings.extend(plugin.warnings)
            if len(bundle.plugins) >= MAX_PLUGINS:
                bundle.warnings.append(
                    f"agent-plugins: loaded at most {MAX_PLUGINS} packages"
                )
                return bundle
    return bundle


def merge_mcp_servers(
    configured: Iterable[MCPServerConfig],
    discovered: Iterable[MCPServerConfig],
) -> tuple[MCPServerConfig, ...]:
    """Prefer explicit config names; append plugin servers until the cap."""
    from .config import MAX_MCP_SERVERS

    merged: list[MCPServerConfig] = []
    seen: set[str] = set()
    for server in configured:
        if server.name in seen:
            continue
        seen.add(server.name)
        merged.append(server)
    for server in discovered:
        if server.name in seen:
            continue
        if len(merged) >= MAX_MCP_SERVERS:
            break
        seen.add(server.name)
        merged.append(server)
    return tuple(merged)


def resolve_workspace(
    cwd: str | Path,
    *,
    skill_paths: Iterable[str | Path] = (),
    mcp_servers: Iterable[MCPServerConfig] = (),
    include_user: bool = True,
    environ: Mapping[str, str] | None = None,
) -> PluginBundle:
    """Discover Agent Plugins MCP servers and preserve explicit skill paths.

    Skills from Agent Plugins packages are discovered by ``mjj.skills`` so the
    skill tool stays complete even when callers forget to merge paths.
    """
    bundle = discover(
        cwd,
        include_user=include_user,
        environ=environ,
    )
    extras = tuple(Path(path).expanduser().resolve() for path in skill_paths)
    return PluginBundle(
        plugins=bundle.plugins,
        skill_dirs=extras,
        mcp_servers=merge_mcp_servers(mcp_servers, bundle.mcp_servers),
        warnings=list(bundle.warnings),
    )


def _load_plugin(root: Path, data_root: Path) -> AgentPlugin | None:
    warnings: list[str] = []
    manifest_path = root / "plugin.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"agent-plugin {root.name}: invalid plugin.json ({exc})")
        return None
    if not isinstance(manifest, dict):
        return None
    unknown = [
        key
        for key in manifest
        if key
        not in {
            "$schema",
            "name",
            "version",
            "description",
            "author",
            "homepage",
            "repository",
            "license",
            "keywords",
            "extensions",
        }
    ]
    for key in unknown:
        warnings.append(f"agent-plugin {root.name}: ignoring unknown field {key!r}")
        manifest.pop(key, None)
    schema = manifest.get("$schema")
    name = manifest.get("name")
    if schema != PLUGIN_SCHEMA:
        warnings.append(
            f"agent-plugin {root.name}: unsupported or missing $schema"
        )
        return None
    if not isinstance(name, str) or not PLUGIN_NAME.fullmatch(name):
        warnings.append(f"agent-plugin {root.name}: invalid name")
        return None
    extensions = manifest.get("extensions")
    if extensions is not None and not isinstance(extensions, dict):
        warnings.append(f"agent-plugin {name}: ignoring non-object extensions")

    skill_dirs: list[Path] = []
    skills_root = root / "skills"
    if skills_root.is_symlink():
        warnings.append(f"agent-plugin {name}: skills path is a symlink")
    elif skills_root.is_file():
        warnings.append(f"agent-plugin {name}: skills must be a directory")
    elif skills_root.is_dir():
        for child in sorted(skills_root.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir() or child.is_symlink():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.is_file() or skill_md.is_symlink():
                continue
            if not _within(root, skill_md):
                warnings.append(
                    f"agent-plugin {name}: skipped skill outside plugin root"
                )
                continue
            skill_dirs.append(child)

    mcp_servers: list[MCPServerConfig] = []
    mcp_path = root / "mcp.json"
    if mcp_path.is_symlink():
        warnings.append(f"agent-plugin {name}: mcp.json is a symlink")
    elif mcp_path.is_dir():
        warnings.append(f"agent-plugin {name}: mcp.json must be a file")
    elif mcp_path.is_file():
        servers, mcp_warnings = _load_mcp(name, root, mcp_path, data_root / name)
        mcp_servers.extend(servers)
        warnings.extend(mcp_warnings)

    version = manifest.get("version") if isinstance(manifest.get("version"), str) else ""
    description = (
        manifest.get("description")
        if isinstance(manifest.get("description"), str)
        else ""
    )
    return AgentPlugin(
        name=name,
        root=root.resolve(),
        version=version,
        description=description[:300],
        skill_dirs=tuple(skill_dirs),
        mcp_servers=tuple(mcp_servers),
        warnings=tuple(warnings),
    )


def _load_mcp(
    plugin_name: str,
    root: Path,
    path: Path,
    data_dir: Path,
) -> tuple[list[MCPServerConfig], list[str]]:
    warnings: list[str] = []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f"agent-plugin {plugin_name}: invalid mcp.json ({exc})"]
    if not isinstance(document, dict):
        return [], [f"agent-plugin {plugin_name}: mcp.json must be an object"]
    if document.get("$schema") != MCP_SCHEMA:
        return [], [
            f"agent-plugin {plugin_name}: unsupported or missing mcp.json $schema"
        ]
    servers_raw = document.get("mcpServers")
    if not isinstance(servers_raw, dict):
        return [], [f"agent-plugin {plugin_name}: mcpServers must be an object"]
    unknown = [key for key in document if key not in {"$schema", "mcpServers"}]
    for key in unknown:
        warnings.append(
            f"agent-plugin {plugin_name}: ignoring unknown mcp.json field {key!r}"
        )

    plugin_root = str(root.resolve())
    plugin_data = str(data_dir.resolve())
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        warnings.append(
            f"agent-plugin {plugin_name}: could not create PLUGIN_DATA ({exc})"
        )

    servers: list[MCPServerConfig] = []
    for raw_name, raw in servers_raw.items():
        label = f"{plugin_name}/{raw_name}"
        if not isinstance(raw_name, str) or not raw_name.strip():
            warnings.append(f"agent-plugin {plugin_name}: skipping unnamed MCP server")
            continue
        if not isinstance(raw, dict):
            warnings.append(f"agent-plugin {label}: server config must be an object")
            continue
        server_type = raw.get("type")
        if server_type in {"streamable-http", "sse"}:
            warnings.append(
                f"agent-plugin {label}: {server_type} transport not supported yet"
            )
            continue
        if server_type != "stdio":
            warnings.append(f"agent-plugin {label}: unknown transport {server_type!r}")
            continue
        try:
            server = _stdio_server(
                plugin_name,
                raw_name.strip(),
                raw,
                root,
                plugin_root,
                plugin_data,
            )
        except ValueError as exc:
            warnings.append(f"agent-plugin {label}: {exc}")
            continue
        servers.append(server)
    return servers, warnings


def _stdio_server(
    plugin_name: str,
    server_name: str,
    raw: dict,
    root: Path,
    plugin_root: str,
    plugin_data: str,
) -> MCPServerConfig:
    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")
    if any(key in raw for key in ("url", "headers")):
        raise ValueError("stdio server must not include remote fields")
    args = raw.get("args", [])
    if args is None:
        args = []
    if not isinstance(args, list) or any(not isinstance(item, str) for item in args):
        raise ValueError("args must be an array of strings")
    env_raw = raw.get("env", {})
    if env_raw is None:
        env_raw = {}
    if not isinstance(env_raw, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in env_raw.items()
    ):
        raise ValueError("env must be a string table")
    if any(key.upper() in {"PLUGIN_ROOT", "PLUGIN_DATA"} for key in env_raw):
        raise ValueError("env must not set PLUGIN_ROOT or PLUGIN_DATA")

    resolved_command = _resolve_command(command.strip(), root)
    expanded_args = tuple(
        _expand(item, plugin_root, plugin_data) for item in args
    )
    expanded_env = {
        key: _expand(value, plugin_root, plugin_data)
        for key, value in env_raw.items()
    }
    expanded_env["PLUGIN_ROOT"] = plugin_root
    expanded_env["PLUGIN_DATA"] = plugin_data

    cwd_raw = raw.get("cwd")
    if cwd_raw is None:
        cwd = root.resolve()
    elif not isinstance(cwd_raw, str) or not cwd_raw.strip():
        raise ValueError("cwd must be a non-empty string when set")
    else:
        cwd = _resolve_cwd(cwd_raw.strip(), root, plugin_root, plugin_data)

    qualified = _server_name(plugin_name, server_name)
    return MCPServerConfig(
        name=qualified,
        command=(resolved_command, *expanded_args),
        cwd=cwd,
        env=tuple(sorted(expanded_env.items())),
    )


def _resolve_command(command: str, root: Path) -> str:
    if command.startswith("./"):
        candidate = (root / command[2:]).resolve()
        if not _within(root, candidate):
            raise ValueError("command escapes plugin root")
        if not candidate.exists():
            raise ValueError(f"command not found: {command}")
        return str(candidate)
    if "/" in command or "\\" in command:
        raise ValueError("command must be bare or begin with ./")
    resolved = shutil.which(command)
    if resolved is None:
        raise ValueError(f"command not found on PATH: {command}")
    return resolved


def _resolve_cwd(
    value: str,
    root: Path,
    plugin_root: str,
    plugin_data: str,
) -> Path:
    expanded = _expand(value, plugin_root, plugin_data)
    if value.startswith("./"):
        candidate = (root / value[2:]).resolve()
        if not _within(root, candidate):
            raise ValueError("cwd escapes plugin root")
        return candidate
    if value == "${PLUGIN_ROOT}" or value.startswith("${PLUGIN_ROOT}/"):
        candidate = Path(expanded).resolve()
        if not _within(root, candidate):
            raise ValueError("cwd escapes plugin root")
        return candidate
    if value == "${PLUGIN_DATA}" or value.startswith("${PLUGIN_DATA}/"):
        data_root = Path(plugin_data).resolve()
        candidate = Path(expanded).resolve()
        if not _within(data_root, candidate) and candidate != data_root:
            raise ValueError("cwd escapes PLUGIN_DATA")
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate
    raise ValueError("cwd must be ./..., ${PLUGIN_ROOT}..., or ${PLUGIN_DATA}...")


def _expand(value: str, plugin_root: str, plugin_data: str) -> str:
    return value.replace("${PLUGIN_ROOT}", plugin_root).replace(
        "${PLUGIN_DATA}", plugin_data
    )


def _server_name(plugin_name: str, server_name: str) -> str:
    raw = f"{plugin_name}__{server_name}"
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    return (cleaned or "plugin")[:64]


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _project_root(cwd: Path) -> Path:
    for directory in (cwd, *cwd.parents):
        try:
            if (directory / ".git").exists():
                return directory
        except OSError:
            continue
    return cwd


__all__ = [
    "AgentPlugin",
    "MCP_SCHEMA",
    "PLUGIN_SCHEMA",
    "PluginBundle",
    "discover",
    "merge_mcp_servers",
    "resolve_workspace",
]
