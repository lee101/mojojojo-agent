from __future__ import annotations

import pytest

from mjj import __version__
from mjj.cli import main


def test_cli_reports_version(capsys) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["--version"])
    assert stopped.value.code == 0
    assert capsys.readouterr().out.strip() == f"mjj {__version__}"
