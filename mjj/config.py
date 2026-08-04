"""Typed configuration with flags > environment > project > user precedence."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
VERBOSITIES = ("low", "medium", "high")
PROVIDERS = ("auto", "openpaths", "openrouter", "openai", "custom")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    provider: str = "auto"
    model: str = "auto"
    effort: str = "high"
    verbosity: str = "low"
    tool_budget: int = 1600
    project_doc_max_bytes: int = 32 * 1024
    auto_next_steps: bool = False
    auto_next_idea: bool = False
    auto_max_turns: int = 0
    disabled_tools: tuple[str, ...] = ()
    skill_paths: tuple[Path, ...] = ()
    files: tuple[Path, ...] = field(default=(), repr=False)

    def public(self) -> dict[str, Any]:
        result = asdict(self)
        result["skill_paths"] = [str(path) for path in self.skill_paths]
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
    candidates = [user_home / "config.toml"]
    project = _project_config(working)
    if project is not None and project not in candidates:
        candidates.append(project)
    if explicit is not None:
        requested = Path(explicit).expanduser().resolve()
        if not requested.is_file():
            raise ConfigError(f"config file does not exist: {requested}")
        candidates.append(requested)

    for path in candidates:
        if not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                document = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"cannot load {path}: {exc}") from exc
        _merge_document(values, document, path)
        loaded.append(path.resolve())

    env_keys = {
        "MJJ_PROVIDER": "provider",
        "MJJ_MODEL": "model",
        "MJJ_EFFORT": "effort",
        "MJJ_VERBOSITY": "verbosity",
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


def _merge_document(values: dict[str, Any], document: dict, path: Path) -> None:
    agent = document.get("agent", {})
    tools = document.get("tools", {})
    skills = document.get("skills", {})
    for section, name in ((agent, "agent"), (tools, "tools"), (skills, "skills")):
        if not isinstance(section, dict):
            raise ConfigError(f"[{name}] in {path} must be a table")
    for key in (
        "provider",
        "model",
        "effort",
        "verbosity",
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


def _validated(values: Mapping[str, Any]) -> Config:
    provider = values.get("provider", Config.provider)
    model = values.get("model", Config.model)
    effort = values.get("effort", Config.effort)
    verbosity = values.get("verbosity", Config.verbosity)
    budget = values.get("tool_budget", Config.tool_budget)
    project_doc_max_bytes = values.get(
        "project_doc_max_bytes", Config.project_doc_max_bytes
    )
    auto_next_steps = values.get("auto_next_steps", Config.auto_next_steps)
    auto_next_idea = values.get("auto_next_idea", Config.auto_next_idea)
    auto_max_turns = values.get("auto_max_turns", Config.auto_max_turns)
    disabled = values.get("disabled_tools", ())
    skill_paths = values.get("skill_paths", ())
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
    return Config(
        provider=provider,
        model=model.strip(),
        effort=effort,
        verbosity=verbosity,
        tool_budget=budget,
        project_doc_max_bytes=project_doc_max_bytes,
        auto_next_steps=auto_next_steps,
        auto_next_idea=auto_next_idea,
        auto_max_turns=auto_max_turns,
        disabled_tools=tuple(dict.fromkeys(item.strip() for item in disabled)),
        skill_paths=tuple(Path(item).expanduser().resolve() for item in skill_paths),
        files=tuple(values.get("files", ())),
    )


def _boolean(value: str, variable: str) -> bool:
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{variable} must be true or false")
