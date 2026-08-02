from __future__ import annotations

import pytest

from mjj import __version__
from mjj.cli import _exec_prompt, main


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
