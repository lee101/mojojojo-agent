"""Deterministic format / lint-fix / typecheck after edits.

Keeps post-write feedback out of the model loop when an installed tool can
already fix or report the problem. Missing binaries degrade to a no-op.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .checkpoints import CheckpointError, store_for
from .tools.base import ToolContext

POST_EDIT_MODES = ("off", "format", "fix", "full")
MAX_REPORT_CHARS = 1_200
MAX_POST_EDIT_LSP_FILES = 3


@dataclass(frozen=True)
class HygieneResult:
    text: str = ""
    ok: bool = True
    formatted: str = ""
    fixed: str = ""
    checkpoint: str = ""
    reports: tuple[str, ...] = ()


def normalize_post_edit(value: object) -> str:
    if value is None:
        return "off"
    if not isinstance(value, str):
        raise ValueError("post_edit must be a string")
    mode = value.strip().lower()
    if mode not in POST_EDIT_MODES:
        raise ValueError(
            "post_edit must be one of " + ", ".join(POST_EDIT_MODES)
        )
    return mode


def project_executable(root: Path, name: str) -> str | None:
    candidates = (
        root / ".venv" / "bin" / name,
        root / ".venv" / "Scripts" / f"{name}.exe",
        root / "venv" / "bin" / name,
        root / "venv" / "Scripts" / f"{name}.exe",
    )
    return next((str(path) for path in candidates if path.is_file()), None)


def project_node_executable(root: Path, name: str) -> str | None:
    candidates = (
        root / "node_modules" / ".bin" / name,
        root / "node_modules" / ".bin" / f"{name}.cmd",
    )
    return next((str(path) for path in candidates if path.is_file()), None)


def formatter_commands(root: Path, paths: list[Path]) -> list[tuple[str, list[str]]]:
    grouped: dict[str, list[Path]] = {}
    for path in paths:
        grouped.setdefault(path.suffix.lower(), []).append(path)
    commands: list[tuple[str, list[str]]] = []

    python_paths = grouped.get(".py", []) + grouped.get(".pyi", [])
    if python_paths:
        ruff = project_executable(root, "ruff") or shutil.which("ruff")
        black = project_executable(root, "black") or shutil.which("black")
        if ruff:
            commands.append(("ruff", [ruff, "format", *map(str, python_paths)]))
        elif black:
            commands.append(("black", [black, "--quiet", *map(str, python_paths)]))

    web_suffixes = {".js", ".jsx", ".ts", ".tsx", ".json", ".css", ".md"}
    web_paths = [path for suffix in web_suffixes for path in grouped.get(suffix, [])]
    prettier = project_node_executable(root, "prettier")
    if web_paths and prettier:
        commands.append(("prettier", [prettier, "--write", *map(str, web_paths)]))

    executable_by_suffix = {
        ".go": ("gofmt", ["gofmt", "-w"]),
        ".rs": ("rustfmt", ["rustfmt"]),
        ".c": ("clang-format", ["clang-format", "-i"]),
        ".cc": ("clang-format", ["clang-format", "-i"]),
        ".cpp": ("clang-format", ["clang-format", "-i"]),
        ".h": ("clang-format", ["clang-format", "-i"]),
        ".hpp": ("clang-format", ["clang-format", "-i"]),
    }
    for suffix, suffix_paths in grouped.items():
        formatter = executable_by_suffix.get(suffix)
        if formatter and shutil.which(formatter[0]):
            commands.append(
                (formatter[0], [*formatter[1], *map(str, suffix_paths)])
            )
    return commands


def fixer_commands(root: Path, paths: list[Path]) -> list[tuple[str, list[str]]]:
    grouped: dict[str, list[Path]] = {}
    for path in paths:
        grouped.setdefault(path.suffix.lower(), []).append(path)
    commands: list[tuple[str, list[str]]] = []

    python_paths = grouped.get(".py", []) + grouped.get(".pyi", [])
    if python_paths:
        ruff = project_executable(root, "ruff") or shutil.which("ruff")
        if ruff:
            commands.append(
                ("ruff-fix", [ruff, "check", "--fix", *map(str, python_paths)])
            )

    web_paths = [
        path
        for suffix in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
        for path in grouped.get(suffix, [])
    ]
    eslint = project_node_executable(root, "eslint")
    if web_paths and eslint:
        commands.append(
            ("eslint-fix", [eslint, "--fix", *map(str, web_paths)])
        )
    return commands


def typecheck_commands(root: Path, paths: list[Path]) -> list[tuple[str, list[str]]]:
    commands: list[tuple[str, list[str]]] = []
    python_paths = [
        path for path in paths if path.suffix.lower() in {".py", ".pyi"}
    ]
    if python_paths:
        ruff = project_executable(root, "ruff") or shutil.which("ruff")
        if ruff:
            commands.append(
                ("ruff", [ruff, "check", *map(str, python_paths)])
            )
        for name, argv in (
            ("ty", ["check"]),
            ("basedpyright", []),
            ("pyright", []),
        ):
            binary = project_executable(root, name) or shutil.which(name)
            if binary:
                commands.append((name, [binary, *argv, *map(str, python_paths)]))
                break

    ts_paths = [
        path
        for path in paths
        if path.suffix.lower() in {".ts", ".tsx", ".mts", ".cts"}
    ]
    if ts_paths and (root / "tsconfig.json").is_file():
        tsc = project_node_executable(root, "tsc") or shutil.which("tsc")
        if tsc:
            commands.append(("tsc", [tsc, "--noEmit", "--pretty", "false"]))
    return commands


def run_mutators(
    ctx: ToolContext,
    paths: list[Path],
    commands: list[tuple[str, list[str]]],
) -> tuple[str, str]:
    store = store_for(ctx.cwd, ctx.state)
    pending = store.begin(paths)
    labels: list[str] = []
    failure = ""
    for label, command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=ctx.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
                env=os.environ.copy(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            failure = f"{label}: {type(exc).__name__}: {exc}"
            break
        if completed.returncode:
            detail = " ".join(completed.stdout.split())[:300]
            failure = f"{label}: {detail or f'exit {completed.returncode}'}"
            break
        labels.append(label)
    if failure:
        checkpoint = store.finish(pending)
        store.undo(checkpoint.identifier)
        raise CheckpointError(f"{failure}; changes restored")
    checkpoint = store.finish(pending)
    return ",".join(labels), checkpoint.identifier


def run_reports(
    ctx: ToolContext, commands: list[tuple[str, list[str]]]
) -> list[str]:
    messages: list[str] = []
    for label, command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=ctx.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
                env=os.environ.copy(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            messages.append(f"{label}: {type(exc).__name__}: {exc}")
            continue
        if completed.returncode:
            detail = " ".join(completed.stdout.split())[:400]
            messages.append(f"{label}: {detail or f'exit {completed.returncode}'}")
    return messages


def apply_post_edit(
    ctx: ToolContext, paths: list[Path], mode: str | None = None
) -> HygieneResult:
    """Format / autofix / report on paths after a successful mutation."""
    resolved = normalize_post_edit(
        mode if mode is not None else ctx.state.get("post_edit", "off")
    )
    if resolved == "off" or not paths:
        return HygieneResult()

    root = ctx.cwd.resolve()
    existing = [
        path
        for path in paths
        if path.is_file() and not path.is_symlink()
    ]
    if not existing:
        return HygieneResult()

    mutators: list[tuple[str, list[str]]] = []
    if resolved in {"format", "fix", "full"}:
        mutators.extend(formatter_commands(root, existing))
    if resolved in {"fix", "full"}:
        mutators.extend(fixer_commands(root, existing))

    formatted = ""
    fixed = ""
    checkpoint = ""
    if mutators:
        if ctx.approve is not None:
            try:
                approved = ctx.approve(
                    "post_edit",
                    {
                        "mode": resolved,
                        "paths": [
                            path.relative_to(root).as_posix() for path in existing
                        ],
                        "tools": [label for label, _ in mutators],
                    },
                )
            except Exception as exc:
                return HygieneResult(
                    text=f"post_edit skipped: approval failed: {exc}",
                    ok=False,
                )
            if not approved:
                return HygieneResult(
                    text="post_edit skipped: denied",
                    ok=False,
                )
        try:
            labels, checkpoint = run_mutators(ctx, existing, mutators)
        except (OSError, CheckpointError, subprocess.SubprocessError) as exc:
            return HygieneResult(text=f"post_edit failed: {exc}", ok=False)
        parts = [label for label in labels.split(",") if label]
        fix_names = {"ruff-fix", "eslint-fix"}
        formatted = ",".join(label for label in parts if label not in fix_names)
        fixed = ",".join(label for label in parts if label in fix_names)
        changed = ctx.state.setdefault("changed-files", set())
        if isinstance(changed, set):
            changed.update(path.relative_to(root).as_posix() for path in existing)

    reports: list[str] = []
    if resolved == "full":
        reports = run_reports(ctx, typecheck_commands(root, existing))
        reports.extend(_lsp_diagnostic_reports(ctx, existing))
    elif resolved == "fix":
        # After autofix, surface remaining lint so the model can finish the job.
        reports = run_reports(
            ctx,
            [
                command
                for command in typecheck_commands(root, existing)
                if command[0] == "ruff"
            ],
        )
        reports.extend(_lsp_diagnostic_reports(ctx, existing, limit=3))

    lines: list[str] = []
    if formatted:
        lines.append(f"format ✓ {formatted}")
    if fixed:
        lines.append(f"fix ✓ {fixed}")
    if checkpoint and (formatted or fixed):
        lines.append(f"post_edit checkpoint {checkpoint}")
    if reports:
        blob = "\n".join(reports)
        if len(blob) > MAX_REPORT_CHARS:
            blob = blob[: MAX_REPORT_CHARS - 20] + "\n… truncated"
        lines.append("post_edit remaining:\n" + blob)
    return HygieneResult(
        text="\n".join(lines),
        ok=not reports,
        formatted=formatted,
        fixed=fixed,
        checkpoint=checkpoint,
        reports=tuple(reports),
    )


def _lsp_diagnostic_reports(
    ctx: ToolContext,
    paths: list[Path],
    *,
    limit: int = MAX_POST_EDIT_LSP_FILES,
) -> list[str]:
    if ctx.state.get("disable-lsp"):
        return []
    try:
        from .lsp import collect_diagnostics, server_for
    except Exception:
        return []
    messages: list[str] = []
    root = ctx.cwd.resolve()
    for path in paths[:limit]:
        if path.stat().st_size > 2 * 1024 * 1024:
            continue
        server = server_for(path)
        if server is None:
            continue
        try:
            diagnostics = collect_diagnostics(
                server, root=root, path=path, timeout=2.0
            )
        except Exception:
            continue
        relative = path.relative_to(root).as_posix()
        for item in diagnostics:
            if item.severity in {"error", "warning"}:
                messages.append(f"lsp: {item.render(relative)}")
        if len(messages) >= 20:
            break
    return messages[:20]
