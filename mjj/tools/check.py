"""Syntax checks now, optional compiler checks in the background."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..checkpoints import CheckpointError, store_for
from ..syntax import validate_path
from .base import ToolContext, ToolResult


@dataclass
class _CompileJob:
    identifier: str
    thread: threading.Thread | None = None
    ok: bool | None = None
    output: str = ""
    milliseconds: float = 0.0


class CheckTool:
    name = "check"
    description = "Syntax-check files now; optionally queue compiler checks."
    parameters = {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 50,
                "description": "Files; defaults to files changed this run or in git.",
            },
            "compile": {
                "type": "boolean",
                "description": "Queue non-blocking language compiler checks.",
            },
            "format": {
                "type": "boolean",
                "description": "Run an installed project formatter before checking.",
            },
            "job": {
                "type": "string",
                "description": "Compiler job id to poll.",
            },
        },
        "additionalProperties": False,
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        job_id = args.get("job")
        if job_id is not None:
            if not isinstance(job_id, str) or not job_id:
                return self._result(ctx, "job must be a non-empty string", ok=False)
            return self._poll(ctx, job_id)
        compile_requested = args.get("compile", False)
        if not isinstance(compile_requested, bool):
            return self._result(ctx, "compile must be true or false", ok=False)
        format_requested = args.get("format", False)
        if not isinstance(format_requested, bool):
            return self._result(ctx, "format must be true or false", ok=False)
        try:
            paths = _resolve_paths(args.get("paths"), ctx)
        except ValueError as exc:
            return self._result(ctx, str(exc), ok=False)
        if not paths:
            return self._result(ctx, "no changed files; pass paths", ok=False)

        formatted = None
        checkpoint = None
        if format_requested:
            commands = _formatter_commands(ctx.cwd.resolve(), paths)
            if not commands:
                return self._result(
                    ctx, "format: no installed formatter for these files", ok=False
                )
            if ctx.approve is not None:
                try:
                    approved = ctx.approve(
                        "format",
                        {
                            "paths": [
                                path.relative_to(ctx.cwd.resolve()).as_posix()
                                for path in paths
                            ],
                            "formatters": [command[0] for command in commands],
                        },
                    )
                except Exception as exc:
                    return self._result(ctx, f"approval failed: {exc}", ok=False)
                if not approved:
                    return self._result(
                        ctx, "format denied", ok=False, denied=True
                    )
            try:
                formatted, checkpoint = _run_formatters(ctx, paths, commands)
            except (OSError, CheckpointError, subprocess.SubprocessError) as exc:
                return self._result(ctx, f"format failed: {exc}", ok=False)

        checks = [
            validate_path(path, label=path.relative_to(ctx.cwd.resolve()).as_posix())
            for path in paths
        ]
        failures = [check for check in checks if check.checked and not check.ok]
        checked = [check for check in checks if check.checked]
        skipped = len(checks) - len(checked)
        if failures:
            output = "\n".join(
                f"FAIL {check.path}:{check.checker}: {check.message}"
                for check in failures
            )
            if formatted:
                output = (
                    f"format ✓ {formatted} · checkpoint {checkpoint}\n" + output
                )
            return self._result(
                ctx,
                output,
                ok=False,
                files=len(paths),
                checked=len(checked),
                failures=len(failures),
                formatted=formatted,
                checkpoint=checkpoint,
            )
        labels = sorted({check.checker for check in checked})
        output = f"syntax ✓ {len(checked)}/{len(paths)}"
        if labels:
            output += " · " + ",".join(labels)
        if skipped:
            output += f" · {skipped} unchecked"
        if formatted:
            output = f"format ✓ {formatted} · checkpoint {checkpoint}\n" + output
            changed = ctx.state.setdefault("changed-files", set())
            if isinstance(changed, set):
                changed.update(
                    path.relative_to(ctx.cwd.resolve()).as_posix() for path in paths
                )

        queued = None
        if compile_requested:
            commands = _compiler_commands(ctx.cwd.resolve(), paths)
            if commands:
                queued = _start_job(ctx, commands)
                output += f"\ncompile {queued} queued · poll check job={queued}"
            else:
                output += "\ncompile: no available checker for these files"
        return self._result(
            ctx,
            output,
            files=len(paths),
            checked=len(checked),
            skipped=skipped,
            compilers=queued,
            formatted=formatted,
            checkpoint=checkpoint,
        )

    def _poll(self, ctx: ToolContext, identifier: str) -> ToolResult:
        jobs = ctx.state.get("check-jobs", {})
        job = jobs.get(identifier) if isinstance(jobs, dict) else None
        if not isinstance(job, _CompileJob):
            return self._result(ctx, f"unknown compiler job: {identifier}", ok=False)
        assert job.thread is not None
        if job.thread.is_alive():
            return self._result(ctx, f"compile {identifier} running", job=identifier)
        status = "✓" if job.ok else "FAIL"
        detail = f"\n{job.output}" if job.output else ""
        return self._result(
            ctx,
            f"compile {identifier} {status} · {job.milliseconds:.1f} ms{detail}",
            ok=bool(job.ok),
            job=identifier,
            milliseconds=round(job.milliseconds, 2),
        )

    @staticmethod
    def _result(ctx: ToolContext, text: str, *, ok: bool = True, **meta) -> ToolResult:
        return ToolResult(
            ctx.ledger.clip(
                "check", text, hint="check fewer paths or poll the compiler job"
            ),
            ok=ok,
            meta=meta,
        )


def _resolve_paths(raw, ctx: ToolContext) -> list[Path]:
    root = ctx.cwd.resolve()
    if raw is None:
        names = _changed_names(ctx)
    else:
        if not isinstance(raw, list) or any(
            not isinstance(item, str) or not item for item in raw
        ):
            raise ValueError("paths must be an array of non-empty strings")
        if len(raw) > 50:
            raise ValueError("paths may contain at most 50 files")
        names = raw
    paths: list[Path] = []
    for name in names:
        path = ctx.resolve(name)
        try:
            path.relative_to(root)
        except ValueError:
            raise ValueError(
                f"check path must stay inside the workspace: {name}"
            ) from None
        if not path.exists():
            raise ValueError(f"path does not exist: {name}")
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and not child.is_symlink():
                    paths.append(child)
                    if len(paths) >= 50:
                        break
        elif path.is_file() and not path.is_symlink():
            paths.append(path)
        if len(paths) >= 50:
            break
    return list(dict.fromkeys(paths))


def _changed_names(ctx: ToolContext) -> list[str]:
    changed = ctx.state.get("changed-files")
    if isinstance(changed, set) and changed:
        return sorted(str(path) for path in changed)
    root = ctx.cwd.resolve()
    names: set[str] = set()
    for command in (
        ["git", "diff", "--name-only", "-z", "--diff-filter=ACMR"],
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"],
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
    ):
        try:
            result = subprocess.run(
                command,
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            names.update(
                item.decode("utf-8", errors="surrogateescape")
                for item in result.stdout.split(b"\0")
                if item
            )
    return sorted(names)[:50]


def _compiler_commands(
    root: Path, paths: list[Path]
) -> list[tuple[str, list[str], dict[str, str]]]:
    commands: list[tuple[str, list[str], dict[str, str]]] = []
    environment = dict(os.environ)
    python_files = [
        str(path) for path in paths if path.suffix.lower() in {".py", ".pyi"}
    ]
    if python_files:
        commands.append(
            (
                "py_compile",
                [sys.executable, "-m", "py_compile", *python_files],
                environment,
            )
        )
        compiler = (
            Path(__file__).resolve().parents[2].parent
            / "mojojojo-compiler"
            / "pixi.toml"
        )
        if compiler.is_file() and shutil.which("pixi"):
            # The Mojo frontend is intentionally sampled: it is an optional
            # deeper signal, not a reason to queue fifty multi-second jobs.
            for path in python_files[:3]:
                commands.append(
                    (
                        "mojojojo-compiler",
                        [
                            "pixi",
                            "run",
                            "--manifest-path",
                            str(compiler),
                            "mjc",
                            "analyze",
                            path,
                        ],
                        environment,
                    )
                )
    executable_by_suffix = {
        ".js": ("node", ["node", "--check"]),
        ".cjs": ("node", ["node", "--check"]),
        ".mjs": ("node", ["node", "--check"]),
        ".sh": ("bash", ["bash", "-n"]),
        ".bash": ("bash", ["bash", "-n"]),
        ".rb": ("ruby", ["ruby", "-c"]),
        ".php": ("php", ["php", "-l"]),
        ".lua": ("luac", ["luac", "-p"]),
        ".c": ("cc", ["cc", "-fsyntax-only"]),
        ".cc": ("c++", ["c++", "-fsyntax-only"]),
        ".cpp": ("c++", ["c++", "-fsyntax-only"]),
    }
    for path in paths:
        item = executable_by_suffix.get(path.suffix.lower())
        if item and shutil.which(item[0]):
            commands.append((item[0], [*item[1], str(path)], environment))
    return commands


def _formatter_commands(
    root: Path, paths: list[Path]
) -> list[tuple[str, list[str]]]:
    grouped: dict[str, list[Path]] = {}
    for path in paths:
        grouped.setdefault(path.suffix.lower(), []).append(path)
    commands: list[tuple[str, list[str]]] = []

    python_paths = grouped.get(".py", []) + grouped.get(".pyi", [])
    if python_paths:
        ruff = _project_executable(root, "ruff") or shutil.which("ruff")
        black = _project_executable(root, "black") or shutil.which("black")
        if ruff:
            commands.append(("ruff", [ruff, "format", *map(str, python_paths)]))
        elif black:
            commands.append(("black", [black, "--quiet", *map(str, python_paths)]))

    web_suffixes = {".js", ".jsx", ".ts", ".tsx", ".json", ".css", ".md"}
    web_paths = [path for suffix in web_suffixes for path in grouped.get(suffix, [])]
    prettier = _project_node_executable(root, "prettier")
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


def _project_executable(root: Path, name: str) -> str | None:
    candidates = (
        root / ".venv" / "bin" / name,
        root / ".venv" / "Scripts" / f"{name}.exe",
        root / "venv" / "bin" / name,
        root / "venv" / "Scripts" / f"{name}.exe",
    )
    return next((str(path) for path in candidates if path.is_file()), None)


def _project_node_executable(root: Path, name: str) -> str | None:
    candidates = (
        root / "node_modules" / ".bin" / name,
        root / "node_modules" / ".bin" / f"{name}.cmd",
    )
    return next((str(path) for path in candidates if path.is_file()), None)


def _run_formatters(
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


def _start_job(
    ctx: ToolContext,
    commands: list[tuple[str, list[str], dict[str, str]]],
) -> str:
    jobs = ctx.state.setdefault("check-jobs", {})
    assert isinstance(jobs, dict)
    identifier = f"c{len(jobs) + 1}"
    job = _CompileJob(identifier)
    jobs[identifier] = job
    root = ctx.cwd.resolve()

    def run() -> None:
        started = time.perf_counter()
        messages: list[str] = []
        passed = True
        with tempfile.TemporaryDirectory(prefix="mjj-check-") as temporary:
            for label, command, environment in commands:
                environment = dict(environment)
                environment["PYTHONPYCACHEPREFIX"] = str(Path(temporary) / "pycache")
                try:
                    completed = subprocess.run(
                        command,
                        cwd=root,
                        env=environment,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=30,
                        check=False,
                    )
                    if completed.returncode:
                        passed = False
                        message = " ".join(completed.stdout.split())[:300]
                        messages.append(
                            f"{label}: {message or f'exit {completed.returncode}'}"
                        )
                except (OSError, subprocess.SubprocessError) as exc:
                    passed = False
                    messages.append(f"{label}: {type(exc).__name__}: {exc}")
        job.ok = passed
        job.output = "\n".join(messages)
        job.milliseconds = (time.perf_counter() - started) * 1000

    job.thread = threading.Thread(target=run, name=f"mjj-{identifier}", daemon=True)
    job.thread.start()
    return identifier


TOOLS = [CheckTool()]
