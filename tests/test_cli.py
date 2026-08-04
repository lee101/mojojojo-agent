from __future__ import annotations

import pytest

from mjj import __version__
from mjj import cli
from mjj.cli import _exec_prompt, main
from mjj.cli import _Heartbeat
import io
import time


def test_cli_reports_version(capsys) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["--version"])
    assert stopped.value.code == 0
    assert capsys.readouterr().out.strip() == f"mjj {__version__}"


def test_exec_prompt_combines_argument_and_bounded_stdin(monkeypatch) -> None:
    class Input:
        def isatty(self) -> bool:
            return False

        def read(self, count: int) -> str:
            return "abcdef"[:count]

    monkeypatch.setattr("sys.stdin", Input())
    assert _exec_prompt("task", max_stdin_chars=3) == (
        "task\n\n<stdin>\nabc\n[stdin truncated at 3 characters]\n</stdin>"
    )
    assert _exec_prompt("-", max_stdin_chars=10) == "abcdef"


def test_exec_accepts_codex_style_cd_after_subcommand(tmp_path, monkeypatch) -> None:
    seen = {}

    def capture(args) -> int:
        seen["cwd"] = args.cwd
        return 0

    monkeypatch.setattr(cli, "cmd_exec", capture)
    assert main(["exec", "-C", str(tmp_path), "task"]) == 0
    assert seen["cwd"] == str(tmp_path)


def test_exec_accepts_provider_model_effort_and_images_after_subcommand(monkeypatch) -> None:
    seen = {}

    def capture(args) -> int:
        seen.update(
            provider=args.provider,
            model=args.model,
            effort=args.effort,
            images=args.images,
        )
        return 0

    monkeypatch.setattr(cli, "cmd_exec", capture)
    assert main([
        "exec", "--provider", "openpaths", "--model", "openpaths/auto-code",
        "--effort", "xhigh", "--image", "reference.png", "task",
    ]) == 0
    assert seen == {
        "provider": "openpaths",
        "model": "openpaths/auto-code",
        "effort": "xhigh",
        "images": ["reference.png"],
    }


def test_exec_accepts_autonomy_and_session_controls_after_subcommand(monkeypatch) -> None:
    seen = {}

    def capture(args) -> int:
        seen.update(
            steps=args.auto_next_steps,
            ideas=args.auto_next_idea,
            turns=args.auto_max_turns,
            fork=args.fork,
            name=args.name,
        )
        return 0

    monkeypatch.setattr(cli, "cmd_exec", capture)
    assert main([
        "exec", "--auto-next-steps", "--auto-next-idea",
        "--auto-max-turns", "3", "--fork", "abc123", "--name", "experiment",
        "task",
    ]) == 0
    assert seen == {
        "steps": True,
        "ideas": True,
        "turns": 3,
        "fork": "abc123",
        "name": "experiment",
    }


def test_login_defaults_to_chatgpt(monkeypatch) -> None:
    seen = {}

    def capture(args) -> int:
        seen["provider"] = args.login_provider
        return 0

    monkeypatch.setattr(cli, "cmd_login", capture)
    assert main(["login"]) == 0
    assert seen == {"provider": "chatgpt"}


def test_import_subcommand_accepts_a_jsonl_path(monkeypatch) -> None:
    seen = {}

    def capture(args) -> int:
        seen["input"] = args.input
        return 0

    monkeypatch.setattr(cli, "cmd_import", capture)
    assert main(["import", "transcript.jsonl"]) == 0
    assert seen == {"input": "transcript.jsonl"}


def test_headless_heartbeat_keeps_long_reasoning_visibly_alive() -> None:
    output = io.StringIO()
    heartbeat = _Heartbeat(output, "openai/auto", interval=0.01)
    heartbeat.start()
    time.sleep(0.025)
    heartbeat.stop()
    rendered = output.getvalue()
    assert "working · openai/auto" in rendered
    assert "still working" in rendered
