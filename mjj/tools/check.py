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

from ..syntax import SyntaxCheck, validate_path
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
        try:
            paths = _resolve_paths(args.get("paths"), ctx)
        except ValueError as exc:
            return self._result(ctx, str(exc), ok=False)
        if not paths:
            return self._result(ctx, "no changed files; pass paths", ok=False)

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
            return self._result(
                ctx,
                output,
                ok=False,
                files=len(paths),
                checked=len(checked),
                failures=len(failures),
            )
        labels = sorted({check.checker for check in checked})
        output = f"syntax ✓ {len(checked)}/{len(paths)}"
        if labels:
            output += " · " + ",".join(labels)
        if skipped:
            output += f" · {skipped} unchecked"

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
