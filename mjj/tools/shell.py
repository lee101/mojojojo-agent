"""Bounded subprocess execution with a small read-only fast path."""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path

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
    executable = Path(command[0]).name
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
    return shlex.join(command)


class ShellTool:
    name = "shell"
    description = "Run a command with merged output, timeout, exit code, and optional cwd."
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
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        command = args.get("command")
        use_shell = args.get("shell", False)
        if not isinstance(use_shell, bool):
            return _result(ctx, "shell must be a boolean", ok=False)
        if isinstance(command, str):
            if not command.strip():
                return _result(ctx, "command must not be empty", ok=False)
            if use_shell:
                argv: str | list[str] = command
                policy_argv = [command]
            else:
                try:
                    policy_argv = shlex.split(command)
                except ValueError as exc:
                    return _result(ctx, f"cannot parse command: {exc}", ok=False)
                if not policy_argv:
                    return _result(ctx, "command must not be empty", ok=False)
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
                timeout=float(timeout),
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - started
            output = _timeout_output(exc.stdout)
            body = _with_status(output, f"timed out after {timeout:g}s", 124)
            return _result(
                ctx,
                body,
                ok=False,
                hint="rerun with a larger timeout or narrower command",
                command=shown,
                cwd=str(cwd),
                exit_code=124,
                timed_out=True,
                seconds=round(elapsed, 3),
            )
        except OSError as exc:
            return _result(
                ctx,
                f"could not run {shown}: {exc}",
                ok=False,
                command=shown,
                cwd=str(cwd),
            )

        elapsed = time.monotonic() - started
        body = _with_status(completed.stdout, None, completed.returncode)
        return _result(
            ctx,
            body,
            ok=completed.returncode == 0,
            hint="rerun a narrower command or redirect output to a file",
            command=shown,
            cwd=str(cwd),
            exit_code=completed.returncode,
            seconds=round(elapsed, 3),
        )


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
