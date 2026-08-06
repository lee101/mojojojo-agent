"""Typed configuration with flags > environment > project > user precedence."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .permissions import PERMISSION_MODES

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
VERBOSITIES = ("low", "medium", "high")
PROVIDERS = ("auto", "deepseek", "openpaths", "openrouter", "openai", "custom")
MAX_MCP_SERVERS = 16
MAX_PLUGINS = 8
PLUGIN_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class MCPServerConfig:
    """One explicitly configured local MCP stdio server."""

    name: str
    command: tuple[str, ...]
    cwd: Path | None = None
    env: tuple[tuple[str, str], ...] = ()
    startup_timeout: float = 10.0
    tool_timeout: float = 120.0
    max_tools: int = 32

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": list(self.command),
            "cwd": str(self.cwd) if self.cwd is not None else None,
            "env_keys": [key for key, _ in self.env],
            "startup_timeout": self.startup_timeout,
            "tool_timeout": self.tool_timeout,
            "max_tools": self.max_tools,
        }


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    provider: str = "auto"
    model: str = "auto"
    effort: str = "high"
    verbosity: str = "low"
    permission_mode: str = "auto"
    tool_budget: int = 1600
    project_doc_max_bytes: int = 32 * 1024
    auto_next_steps: bool = False
    auto_next_idea: bool = False
    auto_max_turns: int = 0
    disabled_tools: tuple[str, ...] = ()
    skill_paths: tuple[Path, ...] = ()
    mcp_servers: tuple[MCPServerConfig, ...] = ()
    plugins: tuple[str, ...] = ()
    files: tuple[Path, ...] = field(default=(), repr=False)

    def public(self) -> dict[str, Any]:
        result = asdict(self)
        result["skill_paths"] = [str(path) for path in self.skill_paths]
        result["mcp_servers"] = [server.public() for server in self.mcp_servers]
        result["plugins"] = list(self.plugins)
        result["files"] = [str(path) for path in self.files]
        return result


def load(
    cwd: str | Path = ".",
    *,
    explicit: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Config:
    """Load config without importing model, auth, or optional tool backends."""
    env = os.environ if environ is None else environ
    working = Path(cwd).expanduser().resolve()
    values: dict[str, Any] = {}
    loaded: list[Path] = []
    user_home = Path(env.get("MJJ_HOME") or "~/.mjj").expanduser()
    candidates = [(user_home / "config.toml", True)]
    requested = Path(explicit).expanduser().resolve() if explicit is not None else None
    if requested is not None and not requested.is_file():
        raise ConfigError(f"config file does not exist: {requested}")
    project = _project_config(working)
    if project is not None and project != candidates[0][0]:
        candidates.append((project, project.resolve() == requested))
    if requested is not None and all(path.resolve() != requested for path, _ in candidates):
        candidates.append((requested, True))

    for path, trusted_plugins in candidates:
        if not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                document = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"cannot load {path}: {exc}") from exc
        _merge_document(
            values,
            document,
            path,
            env,
            allow_plugins=trusted_plugins,
        )
        loaded.append(path.resolve())

    env_keys = {
        "MJJ_PROVIDER": "provider",
        "MJJ_MODEL": "model",
        "MJJ_EFFORT": "effort",
        "MJJ_VERBOSITY": "verbosity",
        "MJJ_PERMISSION_MODE": "permission_mode",
        "MJJ_TOOL_BUDGET": "tool_budget",
        "MJJ_PROJECT_DOC_MAX_BYTES": "project_doc_max_bytes",
        "MJJ_AUTO_MAX_TURNS": "auto_max_turns",
    }
    for variable, key in env_keys.items():
        if env.get(variable, "").strip():
            values[key] = env[variable].strip()
    for variable, key in (
        ("MJJ_AUTO_NEXT_STEPS", "auto_next_steps"),
        ("MJJ_AUTO_NEXT_IDEA", "auto_next_idea"),
    ):
        if variable in env:
            values[key] = _boolean(env[variable], variable)
    if "MJJ_DISABLE_TOOLS" in env:
        values["disabled_tools"] = tuple(
            part.strip()
            for part in env["MJJ_DISABLE_TOOLS"].split(",")
            if part.strip()
        )
    if "MJJ_SKILL_PATHS" in env:
        values["skill_paths"] = tuple(
            Path(part).expanduser()
            for part in env["MJJ_SKILL_PATHS"].split(os.pathsep)
            if part.strip()
        )
    if "MJJ_PLUGINS" in env:
        values["plugins"] = tuple(
            part.strip()
            for part in env["MJJ_PLUGINS"].split(",")
            if part.strip()
        )
    values["files"] = tuple(loaded)
    return _validated(values)


def _project_config(cwd: Path) -> Path | None:
    for directory in (cwd, *cwd.parents):
        candidate = directory / ".mjj" / "config.toml"
        if candidate.is_file():
            return candidate
        if (directory / ".git").exists():
            break
    return None


def _merge_document(
    values: dict[str, Any],
    document: dict,
    path: Path,
    environ: Mapping[str, str],
    *,
    allow_plugins: bool,
) -> None:
    agent = document.get("agent", {})
    tools = document.get("tools", {})
    skills = document.get("skills", {})
    plugins = document.get("plugins", {})
    for section, name in (
        (agent, "agent"),
        (tools, "tools"),
        (skills, "skills"),
        (plugins, "plugins"),
    ):
        if not isinstance(section, dict):
            raise ConfigError(f"[{name}] in {path} must be a table")
    for key in (
        "provider",
        "model",
        "effort",
        "verbosity",
        "permission_mode",
        "project_doc_max_bytes",
        "auto_next_steps",
        "auto_next_idea",
        "auto_max_turns",
    ):
        if key in agent:
            values[key] = agent[key]
    if "budget" in tools:
        values["tool_budget"] = tools["budget"]
    if "disabled" in tools:
        values["disabled_tools"] = tools["disabled"]
    if "paths" in skills:
        raw_paths = skills["paths"]
        if not isinstance(raw_paths, list):
            raise ConfigError(f"skills.paths in {path} must be an array")
        resolved = []
        for item in raw_paths:
            candidate = Path(str(item)).expanduser()
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            resolved.append(candidate.resolve())
        values["skill_paths"] = tuple(resolved)
    if "enabled" in plugins:
        enabled = plugins["enabled"]
        if not allow_plugins and enabled:
            raise ConfigError(
                f"plugins.enabled in project config {path} is not trusted; "
                "enable installed code from ~/.mjj/config.toml, --config, or MJJ_PLUGINS"
            )
        values["plugins"] = enabled
    raw_servers = document.get("mcp_servers", {})
    if not isinstance(raw_servers, dict):
        raise ConfigError(f"[mcp_servers] in {path} must be a table")
    servers = dict(values.get("mcp_servers", {}))
    for raw_name, raw_server in raw_servers.items():
        name = str(raw_name).strip()
        if not name or not isinstance(raw_server, dict):
            raise ConfigError(f"mcp_servers.{raw_name} in {path} must be a table")
        if not isinstance(raw_server.get("enabled", True), bool):
            raise ConfigError(f"mcp_servers.{name}.enabled must be a boolean")
        if raw_server.get("enabled", True) is False:
            servers.pop(name, None)
            continue
        command = raw_server.get("command")
        args = raw_server.get("args", [])
        if isinstance(command, str):
            command = [command]
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(item, str) or not item for item in command)
            or not isinstance(args, list)
            or any(not isinstance(item, str) for item in args)
        ):
            raise ConfigError(
                f"mcp_servers.{name}.command must be a string or non-empty string array"
            )
        raw_env = raw_server.get("env", {})
        env_vars = raw_server.get("env_vars", [])
        if not isinstance(raw_env, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in raw_env.items()
        ):
            raise ConfigError(f"mcp_servers.{name}.env must be a string table")
        if not isinstance(env_vars, list) or any(
            not isinstance(item, str) or not item for item in env_vars
        ):
            raise ConfigError(f"mcp_servers.{name}.env_vars must be a string array")
        expanded_env = dict(raw_env)
        for key in env_vars:
            if key in environ:
                expanded_env[key] = environ[key]
        raw_cwd = raw_server.get("cwd")
        server_cwd = None
        if raw_cwd is not None:
            if not isinstance(raw_cwd, str) or not raw_cwd.strip():
                raise ConfigError(f"mcp_servers.{name}.cwd must be a path string")
            server_cwd = Path(raw_cwd).expanduser()
            if not server_cwd.is_absolute():
                server_cwd = path.parent / server_cwd
            server_cwd = server_cwd.resolve()
        servers[name] = {
            "name": name,
            "command": tuple([*command, *args]),
            "cwd": server_cwd,
            "env": tuple(sorted(expanded_env.items())),
            "startup_timeout": raw_server.get("startup_timeout", 10.0),
            "tool_timeout": raw_server.get("tool_timeout", 120.0),
            "max_tools": raw_server.get("max_tools", 32),
        }
    values["mcp_servers"] = servers


def _validated(values: Mapping[str, Any]) -> Config:
    provider = values.get("provider", Config.provider)
    model = values.get("model", Config.model)
    effort = values.get("effort", Config.effort)
    verbosity = values.get("verbosity", Config.verbosity)
    permission_mode = values.get("permission_mode", Config.permission_mode)
    budget = values.get("tool_budget", Config.tool_budget)
    project_doc_max_bytes = values.get(
        "project_doc_max_bytes", Config.project_doc_max_bytes
    )
    auto_next_steps = values.get("auto_next_steps", Config.auto_next_steps)
    auto_next_idea = values.get("auto_next_idea", Config.auto_next_idea)
    auto_max_turns = values.get("auto_max_turns", Config.auto_max_turns)
    disabled = values.get("disabled_tools", ())
    skill_paths = values.get("skill_paths", ())
    raw_mcp_servers = values.get("mcp_servers", {})
    plugins = values.get("plugins", ())
    if provider not in PROVIDERS:
        raise ConfigError(f"agent.provider must be one of {', '.join(PROVIDERS)}")
    if not isinstance(model, str) or not model.strip():
        raise ConfigError("agent.model must be a non-empty string")
    if effort not in EFFORTS:
        raise ConfigError(f"agent.effort must be one of {', '.join(EFFORTS)}")
    if verbosity not in VERBOSITIES:
        raise ConfigError(
            f"agent.verbosity must be one of {', '.join(VERBOSITIES)}"
        )
    if permission_mode not in PERMISSION_MODES:
        raise ConfigError(
            "agent.permission_mode must be one of " + ", ".join(PERMISSION_MODES)
        )
    if isinstance(budget, bool):
        raise ConfigError("tools.budget must be a positive integer")
    try:
        budget = int(budget)
    except (TypeError, ValueError) as exc:
        raise ConfigError("tools.budget must be a positive integer") from exc
    if budget <= 0:
        raise ConfigError("tools.budget must be a positive integer")
    if isinstance(project_doc_max_bytes, bool):
        raise ConfigError("agent.project_doc_max_bytes must be a non-negative integer")
    try:
        project_doc_max_bytes = int(project_doc_max_bytes)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            "agent.project_doc_max_bytes must be a non-negative integer"
        ) from exc
    if project_doc_max_bytes < 0:
        raise ConfigError("agent.project_doc_max_bytes must be a non-negative integer")
    if not isinstance(auto_next_steps, bool) or not isinstance(auto_next_idea, bool):
        raise ConfigError("agent.auto_next_steps and auto_next_idea must be booleans")
    if isinstance(auto_max_turns, bool):
        raise ConfigError("agent.auto_max_turns must be a non-negative integer")
    try:
        auto_max_turns = int(auto_max_turns)
    except (TypeError, ValueError) as exc:
        raise ConfigError("agent.auto_max_turns must be a non-negative integer") from exc
    if auto_max_turns < 0:
        raise ConfigError("agent.auto_max_turns must be a non-negative integer")
    if not isinstance(disabled, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in disabled
    ):
        raise ConfigError("tools.disabled must be an array of tool names")
    if not isinstance(skill_paths, (list, tuple)):
        raise ConfigError("skills.paths must be an array of paths")
    if not isinstance(plugins, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in plugins
    ):
        raise ConfigError("plugins.enabled must be an array of entry-point names")
    plugins = tuple(dict.fromkeys(item.strip() for item in plugins))
    if len(plugins) > MAX_PLUGINS:
        raise ConfigError(f"plugins.enabled may contain at most {MAX_PLUGINS} names")
    if any(not PLUGIN_NAME.fullmatch(item) for item in plugins):
        raise ConfigError(
            "plugins.enabled names must start with a letter and contain only "
            "letters, numbers, underscores, or hyphens"
        )
    mcp_servers: list[MCPServerConfig] = []
    if not isinstance(raw_mcp_servers, dict):
        raise ConfigError("mcp_servers must be a table")
    for name, raw_server in raw_mcp_servers.items():
        try:
            startup_timeout = float(raw_server["startup_timeout"])
            tool_timeout = float(raw_server["tool_timeout"])
            max_tools = int(raw_server["max_tools"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"mcp_servers.{name} has invalid limits") from exc
        if not 0.1 <= startup_timeout <= 300:
            raise ConfigError(
                f"mcp_servers.{name}.startup_timeout must be between 0.1 and 300"
            )
        if not 0.1 <= tool_timeout <= 3600:
            raise ConfigError(
                f"mcp_servers.{name}.tool_timeout must be between 0.1 and 3600"
            )
        if not 1 <= max_tools <= 128:
            raise ConfigError(f"mcp_servers.{name}.max_tools must be between 1 and 128")
        mcp_servers.append(
            MCPServerConfig(
                name=name,
                command=raw_server["command"],
                cwd=raw_server["cwd"],
                env=raw_server["env"],
                startup_timeout=startup_timeout,
                tool_timeout=tool_timeout,
                max_tools=max_tools,
            )
        )
    if len(mcp_servers) > MAX_MCP_SERVERS:
        raise ConfigError(f"mcp_servers may configure at most {MAX_MCP_SERVERS} servers")
    return Config(
        provider=provider,
        model=model.strip(),
        effort=effort,
        verbosity=verbosity,
        permission_mode=permission_mode,
        tool_budget=budget,
        project_doc_max_bytes=project_doc_max_bytes,
        auto_next_steps=auto_next_steps,
        auto_next_idea=auto_next_idea,
        auto_max_turns=auto_max_turns,
        disabled_tools=tuple(dict.fromkeys(item.strip() for item in disabled)),
        skill_paths=tuple(Path(item).expanduser().resolve() for item in skill_paths),
        mcp_servers=tuple(mcp_servers),
        plugins=plugins,
        files=tuple(values.get("files", ())),
    )


def _boolean(value: str, variable: str) -> bool:
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{variable} must be true or false")


def user_config_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env.get("MJJ_HOME") or "~/.mjj").expanduser() / "config.toml"


def persist_user_agent_settings(
    *,
    model: str | None = None,
    provider: str | None = None,
    effort: str | None = None,
    verbosity: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Write selected interactive defaults into ``$MJJ_HOME/config.toml``.

    Only updates keys that are passed. Preserves unrelated sections and
    unknown ``[agent]`` fields so a ``/model`` pick survives the next launch.
    """
    updates = {
        key: value
        for key, value in (
            ("model", model),
            ("provider", provider),
            ("effort", effort),
            ("verbosity", verbosity),
        )
        if value is not None
    }
    if not updates:
        raise ConfigError("persist_user_agent_settings requires at least one value")
    if "provider" in updates and updates["provider"] not in PROVIDERS:
        raise ConfigError(f"agent.provider must be one of {', '.join(PROVIDERS)}")
    if "effort" in updates and updates["effort"] not in EFFORTS:
        raise ConfigError(f"agent.effort must be one of {', '.join(EFFORTS)}")
    if "verbosity" in updates and updates["verbosity"] not in VERBOSITIES:
        raise ConfigError(
            f"agent.verbosity must be one of {', '.join(VERBOSITIES)}"
        )
    if "model" in updates and not str(updates["model"]).strip():
        raise ConfigError("agent.model must be a non-empty string")

    path = user_config_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(_upsert_agent_toml(existing, updates), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _toml_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _upsert_agent_toml(existing: str, updates: Mapping[str, str]) -> str:
    text = existing.replace("\r\n", "\n")
    match = re.search(r"(?m)^\[agent\]\s*$", text)
    if match is None:
        block = ["[agent]"]
        for key, value in updates.items():
            block.append(f"{key} = {_toml_string(str(value).strip())}")
        suffix = "\n".join(block) + "\n"
        if not text.strip():
            return suffix
        return text.rstrip() + "\n\n" + suffix

    start = match.end()
    next_section = re.search(r"(?m)^\[", text[start:])
    end = start + next_section.start() if next_section else len(text)
    section = text[start:end]
    lines = section.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] = lines[-1] + "\n"

    for key, value in updates.items():
        rendered = f"{key} = {_toml_string(str(value).strip())}\n"
        pattern = re.compile(rf"(?m)^{re.escape(key)}\s*=")
        replaced = False
        for index, line in enumerate(lines):
            if pattern.match(line):
                lines[index] = rendered
                replaced = True
                break
        if not replaced:
            insert_at = len(lines)
            while insert_at > 0 and lines[insert_at - 1].strip() == "":
                insert_at -= 1
            lines.insert(insert_at, rendered)

    return text[:start] + "".join(lines) + text[end:]
