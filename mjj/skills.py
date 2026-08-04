"""Dependency-free SKILL.md discovery shared by the CLI and model tool."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECT_DIRS = (".mjj/skills", ".agents/skills", ".codex/skills", ".claude/skills")
USER_DIRS = ("skills", "~/.agents/skills", "~/.codex/skills", "~/.claude/skills")
BUILTIN_DIR = Path(__file__).with_name("builtin_skills")
MAX_SKILLS = 128
MAX_DEPTH = 4
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    scope: str

    @property
    def qualified_name(self) -> str:
        return f"{self.scope}:{self.name}"

    def body(self) -> str:
        text = self.path.read_text(encoding="utf-8", errors="replace")
        _, _, body = _frontmatter(text)
        return body.strip()

    def files(self, limit: int = 20) -> list[str]:
        root = self.path.parent
        output = []
        for directory, names, filenames in os.walk(root, followlinks=False):
            here = Path(directory)
            depth = len(here.relative_to(root).parts)
            names[:] = sorted(
                name for name in names if depth < 2 and not (here / name).is_symlink()
            )
            for filename in sorted(filenames):
                candidate = here / filename
                if candidate == self.path or candidate.is_symlink():
                    continue
                output.append(candidate.relative_to(root).as_posix())
                if len(output) >= limit:
                    return output
        return output


def discover(
    cwd: str | Path,
    *,
    include_user: bool = True,
    extra_paths: Iterable[str | Path] = (),
) -> list[Skill]:
    """Discover skills in explicit, repository, then user locations."""
    working = Path(cwd).expanduser().resolve()
    roots: list[tuple[str, Path]] = []
    roots.extend(("extra", Path(path).expanduser().resolve()) for path in extra_paths)
    roots.append(("builtin", BUILTIN_DIR))
    project = _project_root(working)
    claude_disabled = _truthy(os.environ.get("MJJ_DISABLE_CLAUDE_CODE", "")) or _truthy(
        os.environ.get("MJJ_DISABLE_CLAUDE_CODE_SKILLS", "")
    )
    roots.extend(
        ("project", project / relative)
        for relative in PROJECT_DIRS
        if not (claude_disabled and relative.startswith(".claude/"))
    )
    if include_user:
        mjj_home = Path(os.environ.get("MJJ_HOME") or "~/.mjj").expanduser()
        roots.append(("user", mjj_home / USER_DIRS[0]))
        roots.extend(
            ("user", Path(path).expanduser())
            for path in USER_DIRS[1:]
            if not (claude_disabled and "/.claude/" in path)
        )

    skills: list[Skill] = []
    seen_paths: set[Path] = set()
    seen_names: set[tuple[str, str]] = set()
    for scope, root in roots:
        for path in _skill_files(root):
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            skill = _parse_skill(resolved, scope)
            if skill is not None and (skill.scope, skill.name) not in seen_names:
                seen_names.add((skill.scope, skill.name))
                skills.append(skill)
                if len(skills) >= MAX_SKILLS:
                    return skills
    return skills


def find(skills: Iterable[Skill], name: str) -> tuple[Skill | None, list[str]]:
    available = list(skills)
    qualified = [skill for skill in available if skill.qualified_name == name]
    if len(qualified) == 1:
        return qualified[0], []
    matches = [skill for skill in available if skill.name == name]
    if len(matches) == 1:
        return matches[0], []
    if len(matches) > 1:
        return None, [skill.qualified_name for skill in matches]
    return None, []


def _project_root(cwd: Path) -> Path:
    for directory in (cwd, *cwd.parents):
        if (directory / ".git").exists():
            return directory
    return cwd


def _skill_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.name == "SKILL.md" else []
    if not root.is_dir() or root.is_symlink():
        return []
    output = []
    for directory, names, filenames in os.walk(root, followlinks=False):
        here = Path(directory)
        depth = len(here.relative_to(root).parts)
        names[:] = sorted(
            name
            for name in names
            if depth < MAX_DEPTH and not (here / name).is_symlink()
        )
        if "SKILL.md" in filenames:
            output.append(here / "SKILL.md")
            names[:] = []
    return output


def _parse_skill(path: Path, scope: str) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    name, description, body = _frontmatter(text)
    name = name or path.parent.name
    if not _NAME.fullmatch(name):
        return None
    if not description:
        description = next(
            (
                line.strip().lstrip("#").strip()
                for line in body.splitlines()
                if line.strip()
            ),
            "Specialized workflow instructions.",
        )
    return Skill(name, description[:500], path, scope)


def _frontmatter(text: str) -> tuple[str, str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "", "", text
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), -1)
    if end < 0:
        return "", "", text
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        if separator and key.strip() in {"name", "description"}:
            metadata[key.strip()] = value.strip().strip("\"'")
    return metadata.get("name", ""), metadata.get("description", ""), "\n".join(lines[end + 1:])


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["Skill", "discover", "find"]
