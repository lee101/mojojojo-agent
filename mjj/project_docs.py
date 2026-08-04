"""Bounded Codex/OpenCode-compatible project instruction discovery."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


DEFAULT_MAX_BYTES = 32 * 1024
GLOBAL_MAX_BYTES = 8 * 1024
FILENAMES = ("AGENTS.override.md", "AGENTS.md", "CLAUDE.md", "CONTEXT.md")


@dataclass(frozen=True)
class ProjectInstructions:
    text: str = ""
    sources: tuple[Path, ...] = ()
    bytes_read: int = 0
    truncated: bool = False


def load(
    cwd: str | Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
    *,
    include_user: bool = False,
    environ: Mapping[str, str] | None = None,
) -> ProjectInstructions:
    """Read one instruction file per directory, from repository root to cwd.

    A local ``AGENTS.override.md`` wins over ``AGENTS.md``; ``CLAUDE.md`` and
    deprecated ``CONTEXT.md`` are compatibility fallbacks. Project files keep
    root-to-CWD semantics. Optional user rules are prepended but consume only
    budget left by project rules, so personal context cannot starve the repo's
    contract. Decoding is loss-tolerant and I/O failures are ignored.
    """
    if max_bytes <= 0:
        return ProjectInstructions()
    working = Path(cwd).expanduser().resolve()
    env = os.environ if environ is None else environ
    filenames = _filenames(env)
    root = _project_root(working)
    directories = _directories(root, working)
    remaining = max_bytes
    parts: list[str] = []
    sources: list[Path] = []
    truncated = False
    for directory in directories:
        path = _find_instruction(directory, filenames)
        if path is None:
            continue
        try:
            with path.open("rb") as handle:
                data = handle.read(remaining + 1)
        except OSError:
            continue
        if len(data) > remaining:
            data = data[:remaining]
            truncated = True
        text = data.decode("utf-8", errors="replace")
        if text.strip():
            parts.append(text)
            sources.append(path.resolve())
            remaining -= len(data)
        if remaining == 0:
            break
    user_parts: list[str] = []
    user_sources: list[Path] = []
    if include_user and remaining > 0:
        user_path = _user_instruction(env, filenames)
        if user_path is not None:
            allowance = min(remaining, GLOBAL_MAX_BYTES)
            try:
                with user_path.open("rb") as handle:
                    data = handle.read(allowance + 1)
            except OSError:
                data = b""
            if len(data) > allowance:
                data = data[:allowance]
                truncated = True
            text = data.decode("utf-8", errors="replace")
            if text.strip():
                user_parts.append(text)
                user_sources.append(user_path.resolve())
                remaining -= len(data)
    return ProjectInstructions(
        text="\n\n".join([*user_parts, *parts]),
        sources=tuple([*user_sources, *sources]),
        bytes_read=max_bytes - remaining,
        truncated=truncated,
    )


def compose(base: str, docs: ProjectInstructions) -> str:
    if not docs.text:
        return base
    return f"{base}\n\n--- project-doc ---\n\n{docs.text}"


def _project_root(cwd: Path) -> Path:
    for directory in (cwd, *cwd.parents):
        try:
            if (directory / ".git").exists():
                return directory
        except OSError:
            continue
    return cwd


def _directories(root: Path, cwd: Path) -> list[Path]:
    try:
        relative = cwd.relative_to(root)
    except ValueError:
        return [cwd]
    directories = [root]
    current = root
    for part in relative.parts:
        current /= part
        directories.append(current)
    return directories


class ScopedProjectDocs:
    """Discover nested instructions once, when tools first enter a subtree."""

    def __init__(
        self,
        cwd: str | Path,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        per_discovery_bytes: int = 8 * 1024,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.cwd = Path(cwd).expanduser().resolve()
        self.root = _project_root(self.cwd)
        self.remaining = max(0, max_bytes)
        self.per_discovery_bytes = max(0, per_discovery_bytes)
        self.filenames = _filenames(os.environ if environ is None else environ)
        self.scanned = set(_directories(self.root, self.cwd))

    def discover(self, paths: Iterable[Path]) -> ProjectInstructions:
        if self.remaining <= 0 or self.per_discovery_bytes <= 0:
            return ProjectInstructions()
        directories: set[Path] = set()
        for raw in paths:
            path = raw.expanduser().resolve()
            try:
                path.relative_to(self.root)
            except ValueError:
                continue
            directory = path if path.is_dir() else path.parent
            directories.update(_directories(self.root, directory))

        allowance = min(self.remaining, self.per_discovery_bytes)
        parts: list[str] = []
        sources: list[Path] = []
        bytes_read = 0
        truncated = False
        for directory in sorted(
            directories, key=lambda item: (len(item.parts), str(item))
        ):
            if directory in self.scanned:
                continue
            self.scanned.add(directory)
            source = _find_instruction(directory, self.filenames)
            if source is None:
                continue
            available = allowance - bytes_read
            if available <= 0:
                truncated = True
                break
            try:
                with source.open("rb") as handle:
                    data = handle.read(available + 1)
            except OSError:
                continue
            if len(data) > available:
                data = data[:available]
                truncated = True
            text = data.decode("utf-8", errors="replace").strip()
            if text:
                relative = source.relative_to(self.root).as_posix()
                parts.append(f"--- scoped project instructions: {relative} ---\n{text}")
                sources.append(source.resolve())
                bytes_read += len(data)
            if truncated:
                break
        self.remaining -= bytes_read
        return ProjectInstructions(
            text="\n\n".join(parts),
            sources=tuple(sources),
            bytes_read=bytes_read,
            truncated=truncated,
        )


def _find_instruction(directory: Path, filenames: tuple[str, ...]) -> Path | None:
    return next(
        (
            directory / name
            for name in filenames
            if (directory / name).is_file()
        ),
        None,
    )


def _filenames(environ: Mapping[str, str]) -> tuple[str, ...]:
    if _claude_prompt_disabled(environ):
        return ("AGENTS.override.md", "AGENTS.md", "CONTEXT.md")
    return FILENAMES


def _user_instruction(
    environ: Mapping[str, str],
    filenames: tuple[str, ...],
) -> Path | None:
    raw_home = environ.get("HOME") or environ.get("USERPROFILE")
    home = Path(raw_home).expanduser() if raw_home else Path.home()
    mjj_home = Path(environ.get("MJJ_HOME") or home / ".mjj").expanduser()
    primary = mjj_home / "AGENTS.md"
    if primary.is_file():
        return primary
    xdg_config = Path(environ.get("XDG_CONFIG_HOME") or home / ".config").expanduser()
    opencode = xdg_config / "opencode" / "AGENTS.md"
    if opencode.is_file():
        return opencode
    if "CLAUDE.md" not in filenames:
        return None
    fallback = home / ".claude" / "CLAUDE.md"
    return fallback if fallback.is_file() else None


def _claude_prompt_disabled(environ: Mapping[str, str]) -> bool:
    return any(
        _truthy(environ.get(name, ""))
        for name in (
            "MJJ_DISABLE_CLAUDE_CODE",
            "MJJ_DISABLE_CLAUDE_CODE_PROMPT",
        )
    )


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "DEFAULT_MAX_BYTES",
    "FILENAMES",
    "GLOBAL_MAX_BYTES",
    "ProjectInstructions",
    "ScopedProjectDocs",
    "compose",
    "load",
]
