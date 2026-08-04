"""Fast source validation with exact parsers and optional tree-sitter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


MAX_SYNTAX_BYTES = 8 * 1024 * 1024
_TREE_SITTER_LANGUAGES = {
    ".bash": "bash",
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "c_sharp",
    ".css": "css",
    ".go": "go",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".lua": "lua",
    ".mojo": "mojo",
    ".php": "php",
    ".ps1": "powershell",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "bash",
    ".sql": "sql",
    ".swift": "swift",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".yaml": "yaml",
    ".yml": "yaml",
}


@dataclass(frozen=True)
class SyntaxCheck:
    path: str
    checked: bool
    ok: bool
    checker: str
    message: str = ""


def validate_source(path: str | Path, data: bytes) -> SyntaxCheck:
    """Validate one source buffer without writing files or starting compilers."""
    label = Path(path).as_posix()
    suffix = Path(path).suffix.lower()
    if len(data) > MAX_SYNTAX_BYTES:
        return SyntaxCheck(label, False, True, "size", "larger than 8 MiB")
    if b"\0" in data[:8192]:
        return SyntaxCheck(label, False, True, "binary")
    try:
        source = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return SyntaxCheck(label, True, False, "utf-8", str(exc))

    try:
        if suffix in {".py", ".pyi"}:
            compile(source, label, "exec")
            return SyntaxCheck(label, True, True, "py_compile")
        if suffix == ".json":
            json.loads(source)
            return SyntaxCheck(label, True, True, "json")
        if suffix == ".toml":
            tomllib.loads(source)
            return SyntaxCheck(label, True, True, "tomllib")
    except (SyntaxError, ValueError, json.JSONDecodeError) as exc:
        return SyntaxCheck(label, True, False, _builtin_checker(suffix), _error(exc))

    language = _TREE_SITTER_LANGUAGES.get(suffix)
    if language is None:
        return SyntaxCheck(label, False, True, "unsupported")
    parser = _tree_sitter_parser(language)
    if parser is None:
        return SyntaxCheck(label, False, True, f"tree-sitter-{language}", "unavailable")
    try:
        root = parser.parse(data).root_node
    except Exception as exc:
        return SyntaxCheck(label, False, True, f"tree-sitter-{language}", str(exc))
    if not root.has_error:
        return SyntaxCheck(label, True, True, f"tree-sitter-{language}")
    problem = _first_error(root)
    if problem is None:
        message = "parse error"
    else:
        row, column = problem.start_point
        message = f"line {row + 1}:{column + 1} {problem.type}"
    return SyntaxCheck(label, True, False, f"tree-sitter-{language}", message)


def validate_path(path: Path, *, label: str | None = None) -> SyntaxCheck:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return SyntaxCheck(label or str(path), True, False, "read", str(exc))
    return validate_source(label or path, data)


def _builtin_checker(suffix: str) -> str:
    return {".py": "py_compile", ".pyi": "py_compile", ".json": "json", ".toml": "tomllib"}.get(suffix, "parser")


def _error(exc: Exception) -> str:
    if isinstance(exc, SyntaxError):
        where = f"line {exc.lineno}:{exc.offset}" if exc.lineno else "syntax error"
        return f"{where} {exc.msg}"
    return " ".join(str(exc).split())[:240]


@lru_cache(maxsize=None)
def _tree_sitter_parser(language: str):
    try:
        from tree_sitter_language_pack import get_parser

        return get_parser(language)
    except (ImportError, LookupError, OSError, RuntimeError):
        return None


def _first_error(node):
    if node.type == "ERROR" or getattr(node, "is_missing", False):
        return node
    for child in node.children:
        if child.has_error or child.type == "ERROR" or getattr(child, "is_missing", False):
            found = _first_error(child)
            if found is not None:
                return found
    return None
