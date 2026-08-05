"""Bounded subprocess execution with a small read-only fast path."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..platforms import command_name, display_command, split_command
from .base import ToolContext, ToolResult

_SAFE_COMMANDS = {
    "basename",
    "cat",
    "cut",
    "dirname",
    "echo",
    "false",
    "file",
    "grep",
    "head",
    "id",
    "ls",
    "nl",
    "paste",
    "pwd",
    "readlink",
    "realpath",
    "rev",
    "rg",
    "seq",
    "stat",
    "tail",
    "tr",
    "true",
    "uname",
    "uniq",
    "wc",
    "which",
    "whoami",
}
_SAFE_GIT = {
    "blame",
    "describe",
    "diff",
    "grep",
    "log",
    "ls-files",
    "ls-tree",
    "rev-parse",
    "show",
    "status",
}
_UNSAFE_FIND = {
    "-delete",
    "-exec",
    "-execdir",
    "-fls",
    "-fprint",
    "-fprint0",
    "-fprintf",
    "-ok",
    "-okdir",
}
_UNSAFE_RG = {"--hostname-bin", "--pre", "--search-zip", "-z"}
_UNSAFE_GIT_OPTIONS = {"--ext-diff", "--output", "--paginate", "-p", "--textconv"}
_SHELL_OPERATORS = {"&&", "||", "|", ";", "&", ">", ">>", "<", "<<"}


@dataclass
class _ShellJob:
    identifier: str
    thread: threading.Thread | None = None
    outcome: "_CommandOutcome | None" = None
    process: subprocess.Popen | None = None
    command: str = ""
    started: float = field(default_factory=time.monotonic)
    stopped: bool = False


@dataclass(frozen=True)
class _CommandOutcome:
    body: str
    ok: bool
    exit_code: int
    timed_out: bool
    seconds: float


def _result(
    ctx: ToolContext,
    text: str,
    *,
    ok: bool = True,
    hint: str = "",
    **meta: object,
) -> ToolResult:
    return ToolResult(
        ctx.ledger.clip("shell", text, hint),
        ok=ok,
        meta=dict(meta),
    )


def _safe(command: list[str], use_shell: bool) -> bool:
    if use_shell or not command:
        return False
    executable = command_name(command[0])
    if executable in _SAFE_COMMANDS:
        if executable == "rg":
            return not any(
                arg in _UNSAFE_RG
                or any(arg.startswith(option + "=") for option in _UNSAFE_RG)
                for arg in command[1:]
            )
        return True
    if executable == "find":
        return not any(arg in _UNSAFE_FIND for arg in command[1:])
    if executable != "git":
        return False
    if any(
        arg in _UNSAFE_GIT_OPTIONS
        or arg.startswith("--output=")
        for arg in command[1:]
    ):
        return False
    index = 1
    while index < len(command) and command[index].startswith("-"):
        option = command[index]
        if option in {"-C", "-c", "--config-env", "--exec-path", "--git-dir", "--work-tree"}:
            return False
        if option.startswith(("-C", "-c", "--config-env=", "--exec-path=", "--git-dir=", "--work-tree=")):
            return False
        index += 1
    return index < len(command) and command[index] in _SAFE_GIT


def _display(command: str | list[str]) -> str:
    if isinstance(command, str):
        return command
    return display_command(command)


class ShellTool:
    name = "shell"
    description = (
        "Run or queue argv with merged output, timeout, exit code, and optional cwd. "
        "Strings use host argv rules, not shell code; use shell=true for &&, pipes or redirects."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}, "minItems": 1},
                ],
                "description": "Command string or argv",
            },
            "cwd": {"type": "string", "description": "Working directory"},
            "timeout": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 120,
                "description": "Seconds",
            },
            "shell": {
                "type": "boolean",
                "default": False,
                "description": "Interpret a string with the system shell",
            },
            "background": {
                "type": "boolean",
                "description": "Queue without blocking; returns a job id.",
            },
            "job": {
                "type": "string",
                "description": "Poll a queued shell job.",
            },
        },
        "additionalProperties": False,
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        job_id = args.get("job")
        if job_id is not None:
            if not isinstance(job_id, str) or not job_id:
                return _result(ctx, "job must be a non-empty string", ok=False)
            if args.get("command") is not None:
                return _result(ctx, "job cannot be combined with command", ok=False)
            return self._poll(ctx, job_id)
        command = args.get("command")
        use_shell = args.get("shell", False)
        if not isinstance(use_shell, bool):
            return _result(ctx, "shell must be a boolean", ok=False)
        background = args.get("background", False)
        if not isinstance(background, bool):
            return _result(ctx, "background must be a boolean", ok=False)
        if isinstance(command, str):
            if not command.strip():
                return _result(ctx, "command must not be empty", ok=False)
            if use_shell:
                argv: str | list[str] = command
                policy_argv = [command]
            else:
                try:
                    policy_argv = split_command(command)
                except ValueError as exc:
                    return _result(ctx, f"cannot parse command: {exc}", ok=False)
                if not policy_argv:
                    return _result(ctx, "command must not be empty", ok=False)
                if any(
                    part in _SHELL_OPERATORS
                    or part.startswith((">", "<"))
                    for part in policy_argv
                ):
                    return _result(
                        ctx,
                        "command contains shell operators but shell=false; "
                        "use an argv array for one command or set shell=true",
                        ok=False,
                    )
                argv = policy_argv
        elif isinstance(command, list) and command and all(
            isinstance(part, str) for part in command
        ):
            if use_shell:
                return _result(
                    ctx, "shell=true requires command to be a string", ok=False
                )
            argv = command
            policy_argv = command
        else:
            return _result(
                ctx, "command must be a string or non-empty string array", ok=False
            )

        timeout = args.get("timeout", 120)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
        ):
            return _result(ctx, "timeout must be a positive number", ok=False)
        cwd_arg = args.get("cwd")
        if cwd_arg is not None and not isinstance(cwd_arg, str):
            return _result(ctx, "cwd must be a string", ok=False)
        cwd = ctx.resolve(cwd_arg) if cwd_arg else ctx.cwd
        if not cwd.is_dir():
            return _result(ctx, f"not a directory: {cwd_arg or cwd}", ok=False)

        shown = _display(command)
        if not _safe(policy_argv, use_shell) and ctx.approve is not None:
            request = {
                "command": shown,
                "cwd": str(cwd),
                "shell": use_shell,
                "timeout": timeout,
            }
            try:
                approved = ctx.approve("shell", request)
            except Exception as exc:
                return _result(ctx, f"approval failed: {exc}", ok=False)
            if not approved:
                return _result(
                    ctx,
                    f"command denied: {shown}",
                    ok=False,
                    denied=True,
                    command=shown,
                )

        if background:
            identifier = _start_shell_job(
                ctx,
                argv,
                cwd=cwd,
                use_shell=use_shell,
                timeout=float(timeout),
                shown=shown,
            )
            if identifier is None:
                return _result(
                    ctx,
                    "shell job limit reached; poll an existing job",
                    ok=False,
                )
            return _result(
                ctx,
                f"shell {identifier} queued · poll shell job={identifier}",
                command=shown,
                cwd=str(cwd),
                job=identifier,
                background=True,
            )
        outcome = _execute(
            argv,
            cwd=cwd,
            use_shell=use_shell,
            timeout=float(timeout),
            shown=shown,
        )
        return _result(
            ctx,
            outcome.body,
            ok=outcome.ok,
            hint="rerun a narrower command or redirect output to a file",
            command=shown,
            cwd=str(cwd),
            exit_code=outcome.exit_code,
            timed_out=outcome.timed_out,
            seconds=round(outcome.seconds, 3),
        )

    @staticmethod
    def _poll(ctx: ToolContext, identifier: str) -> ToolResult:
        jobs = ctx.state.get("shell-jobs", {})
        job = jobs.get(identifier) if isinstance(jobs, dict) else None
        if not isinstance(job, _ShellJob):
            return _result(ctx, f"unknown shell job: {identifier}", ok=False)
        assert job.thread is not None
        if job.thread.is_alive() or job.outcome is None:
            return _result(ctx, f"shell {identifier} running", job=identifier)
        outcome = job.outcome
        return _result(
            ctx,
            outcome.body,
            ok=outcome.ok,
            hint="rerun a narrower command or redirect output to a file",
            job=identifier,
            exit_code=outcome.exit_code,
            timed_out=outcome.timed_out,
            seconds=round(outcome.seconds, 3),
        )


def _execute(
    argv: str | list[str],
    *,
    cwd: Path,
    use_shell: bool,
    timeout: float,
    shown: str,
) -> _CommandOutcome:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            shell=use_shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        output = _timeout_output(exc.stdout)
        body = _with_status(output, f"timed out after {timeout:g}s", 124)
        return _CommandOutcome(body, False, 124, True, elapsed)
    except OSError as exc:
        elapsed = time.monotonic() - started
        return _CommandOutcome(
            f"could not run {shown}: {exc}", False, 127, False, elapsed
        )
    elapsed = time.monotonic() - started
    return _CommandOutcome(
        _with_status(completed.stdout, None, completed.returncode),
        completed.returncode == 0,
        completed.returncode,
        False,
        elapsed,
    )


def _start_shell_job(
    ctx: ToolContext,
    argv: str | list[str],
    *,
    cwd: Path,
    use_shell: bool,
    timeout: float,
    shown: str,
) -> str | None:
    jobs = ctx.state.setdefault("shell-jobs", {})
    assert isinstance(jobs, dict)
    if len(jobs) >= 32:
        completed = [
            key
            for key, value in jobs.items()
            if isinstance(value, _ShellJob)
            and value.thread is not None
            and not value.thread.is_alive()
        ]
        for key in completed[: max(1, len(jobs) - 31)]:
            jobs.pop(key, None)
        if len(jobs) >= 32:
            return None
    sequence = 1
    while f"s{sequence}" in jobs:
        sequence += 1
    identifier = f"s{sequence}"
    job = _ShellJob(identifier, command=shown)
    jobs[identifier] = job

    def run() -> None:
        job.outcome = _execute_job(
            job,
            argv,
            cwd=cwd,
            use_shell=use_shell,
            timeout=timeout,
            shown=shown,
        )

    job.thread = threading.Thread(
        target=run, name=f"mjj-shell-{identifier}", daemon=True
    )
    job.thread.start()
    return identifier


def _execute_job(
    job: _ShellJob,
    argv: str | list[str],
    *,
    cwd: Path,
    use_shell: bool,
    timeout: float,
    shown: str,
) -> _CommandOutcome:
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            shell=use_shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            env=os.environ.copy(),
        )
        job.process = process
        if job.stopped and process.poll() is None:
            process.terminate()
        output, _ = process.communicate(timeout=timeout)
        elapsed = time.monotonic() - started
        note = "stopped by operator" if job.stopped else None
        return _CommandOutcome(
            _with_status(output, note, process.returncode),
            process.returncode == 0 and not job.stopped,
            process.returncode,
            False,
            elapsed,
        )
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
        elapsed = time.monotonic() - started
        return _CommandOutcome(
            _with_status(output, f"timed out after {timeout:g}s", 124),
            False,
            124,
            True,
            elapsed,
        )
    except OSError as exc:
        elapsed = time.monotonic() - started
        return _CommandOutcome(
            f"could not run {shown}: {exc}", False, 127, False, elapsed
        )
    finally:
        job.process = None


def describe_jobs(ctx: ToolContext) -> list[str]:
    """Return bounded operator-facing state without adding model schema cost."""
    jobs = ctx.state.get("shell-jobs", {})
    if not isinstance(jobs, dict):
        return []
    lines = []
    for identifier, job in jobs.items():
        if not isinstance(job, _ShellJob):
            continue
        running = job.thread is not None and job.thread.is_alive()
        status = "running" if running else "stopped" if job.stopped else "done"
        seconds = time.monotonic() - job.started
        lines.append(f"{identifier} · {status} · {seconds:.1f}s · {job.command[:160]}")
    return lines[:32]


def stop_jobs(ctx: ToolContext) -> int:
    """Terminate live background processes; completed jobs remain pollable."""
    jobs = ctx.state.get("shell-jobs", {})
    if not isinstance(jobs, dict):
        return 0
    stopped = 0
    for job in jobs.values():
        if not isinstance(job, _ShellJob) or job.thread is None or not job.thread.is_alive():
            continue
        job.stopped = True
        process = job.process
        if process is not None and process.poll() is None:
            process.terminate()
        stopped += 1
    return stopped


def _timeout_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def _with_status(output: str, note: str | None, exit_code: int) -> str:
    lines = output.rstrip("\n")
    suffix = []
    if note:
        suffix.append(note)
    suffix.append(f"exit code: {exit_code}")
    if lines:
        return f"{lines}\n" + "\n".join(suffix)
    return "\n".join(suffix)


TOOLS = [ShellTool()]
