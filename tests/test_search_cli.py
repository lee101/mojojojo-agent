from __future__ import annotations

import json

from mjj.search.cli import main


def test_search_cli_plain_and_json(tmp_path, capsys):
    (tmp_path / "worker.py").write_text(
        "def worker_bootstrap(node):\n    return node\n", encoding="utf-8"
    )
    assert main(["workerBootstrap", "--root", str(tmp_path)]) == 0
    assert "worker.py:1" in capsys.readouterr().out

    assert main(["worker_bootstrap", "--root", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["path"] == "worker.py"
    assert payload[0]["line"] == 1
    assert "literal" in payload[0]["sources"]


def test_search_cli_has_rg_compatible_no_match_exit(tmp_path, capsys):
    (tmp_path / "x.py").write_text("value = 1\n", encoding="utf-8")
    assert main(["absent_symbol", "--root", str(tmp_path)]) == 1
    assert capsys.readouterr().out.strip() == "no matches"


def test_search_cli_rejects_scope_escape(tmp_path, capsys):
    assert main(["x", "../outside", "--root", str(tmp_path)]) == 2
    assert "inside the root" in capsys.readouterr().err


def test_search_cli_uses_excluded_file_fallback_after_a_miss(tmp_path, capsys):
    (tmp_path / ".gitignore").write_text("generated.py\n")
    (tmp_path / "visible.py").write_text("ordinary = True\n")
    (tmp_path / "generated.py").write_text("hidden_quasar_needle = True\n")

    assert main(["hidden_quasar_needle", "--root", str(tmp_path)]) == 0
    assert "generated.py:1" in capsys.readouterr().out
