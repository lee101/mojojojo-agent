from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from mjj.ledger import Budget, Ledger
from mjj.model import ModelClient
from mjj.subagents import (
    SubagentResult,
    SubagentRunner,
    SubagentTask,
    _Worktree,
    validate_tasks,
)
from mjj.tools.base import ToolContext
from mjj.tools.delegate import DelegateTool


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(
        root,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "base",
    )
    return root


def test_validate_tasks_is_bounded() -> None:
    tasks = validate_tasks([{"prompt": "review auth", "role": "reviewer"}])
    assert tasks == [SubagentTask("review auth", "reviewer")]

    with pytest.raises(ValueError, match="at most 4"):
        validate_tasks([{"prompt": str(index)} for index in range(5)])
    with pytest.raises(ValueError, match="role"):
        validate_tasks([{"prompt": "x", "role": "manager"}])


def test_parallel_runner_returns_input_order(monkeypatch, tmp_path) -> None:
    runner = SubagentRunner(ModelClient())

    def one(task, cwd):
        time.sleep(0.02 if task.prompt == "first" else 0)
        return SubagentResult(task.prompt, task.role, True, answer=task.prompt)

    monkeypatch.setattr(runner, "_one", one)
    results = runner.run(
        [SubagentTask("first"), SubagentTask("second")],
        tmp_path,
    )

    assert [result.answer for result in results] == ["first", "second"]


def test_parallel_runner_isolates_one_crashed_task(monkeypatch, tmp_path) -> None:
    runner = SubagentRunner(ModelClient())

    def one(task, cwd):
        if task.prompt == "broken":
            raise OSError("session storage unavailable")
        return SubagentResult("ok", task.role, True, answer=task.prompt)

    monkeypatch.setattr(runner, "_one", one)
    results = runner.run(
        [SubagentTask("broken"), SubagentTask("healthy")],
        tmp_path,
    )

    assert [result.ok for result in results] == [False, True]
    assert "session storage unavailable" in results[0].error
    assert results[1].answer == "healthy"


def test_subagent_runner_rejects_unbounded_configuration() -> None:
    with pytest.raises(ValueError, match="max_steps"):
        SubagentRunner(ModelClient(), max_steps=0)
    with pytest.raises(ValueError, match="at most 4"):
        SubagentRunner(ModelClient()).run(
            [SubagentTask(str(index)) for index in range(5)],
            Path.cwd(),
        )
    with pytest.raises(ValueError, match="role"):
        SubagentRunner(ModelClient()).run(
            [SubagentTask("review", "manager")],
            Path.cwd(),
        )
    with pytest.raises(ValueError, match="prompt exceeds"):
        SubagentRunner(ModelClient()).run(
            [SubagentTask("x" * (16 * 1024 + 1))],
            Path.cwd(),
        )


def test_worker_commit_excludes_parent_baseline_changes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    root = _repo(tmp_path)
    (root / "tracked.txt").write_text("user baseline\n", encoding="utf-8")
    (root / "untracked.txt").write_text("also baseline\n", encoding="utf-8")

    worktree = _Worktree.create(root, "worker-test")
    try:
        assert (worktree.path / "tracked.txt").read_text() == "user baseline\n"
        assert (worktree.path / "untracked.txt").read_text() == "also baseline\n"
        (worktree.path / "worker.txt").write_text("worker delta\n", encoding="utf-8")
        _git(worktree.path, "add", "worker.txt")
        _git(
            worktree.path,
            "-c",
            "user.name=child",
            "-c",
            "user.email=child@example.com",
            "commit",
            "-m",
            "child committed early",
        )

        commit, ref = worktree.capture("add worker output")
        changed = _git(
            worktree.path,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            commit,
        )

        assert changed == "worker.txt"
        assert _git(root, "rev-parse", ref) == commit
    finally:
        worktree.close()

    assert not worktree.path.exists()
    assert (root / "tracked.txt").read_text() == "user baseline\n"


def test_delegate_tool_uses_ledger_and_structured_metadata(tmp_path) -> None:
    class Runner:
        def run(self, tasks, cwd):
            assert tasks == [SubagentTask("review this", "reviewer")]
            return [
                SubagentResult(
                    "abc",
                    "reviewer",
                    True,
                    answer="looks sound",
                    session_id="session-1",
                )
            ]

    ledger = Ledger(Budget(default=200))
    ctx = ToolContext(tmp_path, ledger)
    ctx.state["subagent-runner"] = Runner()

    result = DelegateTool().run(
        {"tasks": [{"prompt": "review this", "role": "reviewer"}]},
        ctx,
    )

    assert result.ok is True
    assert "looks sound" in result.output
    assert result.meta == {"tasks": 1, "commits": [], "sessions": ["session-1"]}
    assert ledger.tool_calls == 1


def test_delegate_tool_merges_child_usage(tmp_path) -> None:
    class Runner:
        def run(self, tasks, cwd):
            result = SubagentResult("abc", "reviewer", True, answer="done")
            result.usage.requests = 2
            result.usage.input_tokens = 30
            result.usage.output_tokens = 7
            return [result]

    client = ModelClient()
    ctx = ToolContext(tmp_path, Ledger())
    ctx.state.update({"subagent-runner": Runner(), "model-client": client})

    result = DelegateTool().run({"tasks": [{"prompt": "review"}]}, ctx)

    assert result.ok is True
    assert client.usage.requests == 2
    assert client.usage.input_tokens == 30
    assert client.usage.output_tokens == 7


def test_subagent_clients_have_output_ceiling() -> None:
    runner = SubagentRunner(ModelClient(model="gpt-test"), max_output_tokens=321)
    client = runner._client("child")

    assert client.max_output_tokens == 321
    assert client.cache_key == "mjj-subagent-child"
