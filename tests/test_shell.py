import os
import subprocess
import sys
import time
from pathlib import Path

from mjj.ledger import Budget, Ledger
from mjj.tools.base import ToolContext
from mjj.tools.shell import ShellTool


def test_background_shell_returns_immediately_and_can_be_polled(tmp_path) -> None:
    ctx = context(tmp_path)
    started = time.monotonic()

    queued = ShellTool().run(
        {
            "command": [
                sys.executable,
                "-c",
                "import time; time.sleep(0.4); print('background complete')",
            ],
            "background": True,
        },
        ctx,
    )

    assert queued.ok
    assert time.monotonic() - started < 0.2
    identifier = queued.meta["job"]
    job = ctx.state["shell-jobs"][identifier]
    job.thread.join(timeout=3)
    completed = ShellTool().run({"job": identifier}, ctx)
    assert completed.ok
    assert "background complete" in completed.output
    assert completed.meta["exit_code"] == 0


def test_background_shell_timeout_is_reported_on_poll(tmp_path) -> None:
    ctx = context(tmp_path)
    queued = ShellTool().run(
        {
            "command": [sys.executable, "-c", "import time; time.sleep(1)"],
            "timeout": 0.05,
            "background": True,
        },
        ctx,
    )
    identifier = queued.meta["job"]
    ctx.state["shell-jobs"][identifier].thread.join(timeout=3)

    completed = ShellTool().run({"job": identifier}, ctx)

    assert not completed.ok
    assert completed.meta["timed_out"] is True
    assert completed.meta["exit_code"] == 124


def context(tmp_path: Path, approve=None, *, budget: int = 1600) -> ToolContext:
    return ToolContext(
        tmp_path,
        Ledger(Budget(shell=budget)),
        approve=approve,
    )


def test_safe_command_does_not_request_approval(tmp_path):
    def reject_call(_name, _args):
        raise AssertionError("approval should not be requested")

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    result = ShellTool().run(
        {"command": ["git", "status", "--short"]},
        context(tmp_path, reject_call),
    )

    assert result.ok
    assert result.output == "exit code: 0"


def test_unsafe_command_uses_approval_and_can_be_denied(tmp_path):
    requests = []

    def deny(name, args):
        requests.append((name, args))
        return False

    result = ShellTool().run(
        {"command": [sys.executable, "-c", "print('no')"]},
        context(tmp_path, deny),
    )

    assert not result.ok
    assert result.meta["denied"]
    assert requests[0][0] == "shell"
    assert requests[0][1]["shell"] is False


def test_string_command_is_not_interpolated_without_shell_true(tmp_path, monkeypatch):
    monkeypatch.setenv("MJJ_SHELL_SENTINEL", "expanded")

    if os.name == "nt":
        direct_command = (
            f'"{sys.executable}" -c "print(\'%MJJ_SHELL_SENTINEL%\')"'
        )
        expanded_command = "echo %MJJ_SHELL_SENTINEL%"
        literal = "%MJJ_SHELL_SENTINEL%"
    else:
        direct_command = "echo '$MJJ_SHELL_SENTINEL'"
        expanded_command = 'echo "$MJJ_SHELL_SENTINEL"'
        literal = "$MJJ_SHELL_SENTINEL"
    direct = ShellTool().run({"command": direct_command}, context(tmp_path))
    expanded = ShellTool().run(
        {"command": expanded_command, "shell": True}, context(tmp_path)
    )

    assert literal in direct.output
    assert "expanded" in expanded.output


def test_compound_string_is_rejected_instead_of_turning_operators_into_arguments(tmp_path):
    result = ShellTool().run(
        {"command": "python -c pass && python -c pass"}, context(tmp_path)
    )
    assert not result.ok
    assert "shell=false" in result.output


def test_shell_merges_stderr_reports_exit_and_honours_cwd(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    code = "import os,sys; print(os.path.basename(os.getcwd())); print('bad', file=sys.stderr); raise SystemExit(3)"

    result = ShellTool().run(
        {"command": [sys.executable, "-c", code], "cwd": "child"},
        context(tmp_path),
    )

    assert not result.ok
    assert "child" in result.output
    assert "bad" in result.output
    assert result.output.endswith("exit code: 3")
    assert result.meta["exit_code"] == 3


def test_shell_timeout_and_output_clipping(tmp_path):
    timeout = ShellTool().run(
        {
            "command": [sys.executable, "-c", "import time; time.sleep(1)"],
            "timeout": 0.02,
        },
        context(tmp_path),
    )
    clipped_ctx = context(tmp_path, budget=20)
    clipped = ShellTool().run(
        {"command": [sys.executable, "-c", "print('x' * 1000)"]},
        clipped_ctx,
    )

    assert not timeout.ok
    assert timeout.meta["timed_out"]
    assert timeout.meta["exit_code"] == 124
    assert "timed out" in timeout.output
    assert clipped.ok
    assert clipped_ctx.ledger.drops
