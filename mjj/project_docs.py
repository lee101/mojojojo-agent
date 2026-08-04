"""Bounded Codex-compatible ``AGENTS.md`` instruction discovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_MAX_BYTES = 32 * 1024
FILENAMES = ("AGENTS.override.md", "AGENTS.md")


@dataclass(frozen=True)
class ProjectInstructions:
    text: str = ""
    sources: tuple[Path, ...] = ()
    bytes_read: int = 0
    truncated: bool = False


def load(cwd: str | Path, max_bytes: int = DEFAULT_MAX_BYTES) -> ProjectInstructions:
    """Read one instruction file per directory, from repository root to cwd.

    A local ``AGENTS.override.md`` wins over ``AGENTS.md`` in the same
    directory. The byte budget applies across every file and decoding is
    loss-tolerant so project guidance can never prevent the agent starting.
    """
    if max_bytes <= 0:
        return ProjectInstructions()
    working = Path(cwd).expanduser().resolve()
    root = _project_root(working)
    directories = _directories(root, working)
    remaining = max_bytes
    parts: list[str] = []
    sources: list[Path] = []
    truncated = False
    for directory in directories:
        path = next((directory / name for name in FILENAMES if (directory / name).is_file()), None)
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
    return ProjectInstructions(
        text="\n\n".join(parts),
        sources=tuple(sources),
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
    ) -> None:
        self.cwd = Path(cwd).expanduser().resolve()
        self.root = _project_root(self.cwd)
        self.remaining = max(0, max_bytes)
        self.per_discovery_bytes = max(0, per_discovery_bytes)
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
            source = next(
                (
                    directory / name
                    for name in FILENAMES
                    if (directory / name).is_file()
                ),
                None,
            )
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


__all__ = [
    "DEFAULT_MAX_BYTES",
    "ProjectInstructions",
    "ScopedProjectDocs",
    "compose",
    "load",
]
