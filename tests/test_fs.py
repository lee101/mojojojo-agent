from pathlib import Path

from mjj.ledger import Budget, Ledger
from mjj.repo_map import render_repo_map
from mjj.search.index import build_index
from mjj.tools.base import ToolContext
from mjj.tools.fs import ListTool, ReadTool


def context(tmp_path: Path, *, read_budget: int = 2400) -> ToolContext:
    return ToolContext(tmp_path, Ledger(Budget(read=read_budget)))


def test_read_range_is_line_numbered(tmp_path):
    (tmp_path / "sample.txt").write_text("one\ntwo\nthree\n")

    result = ReadTool().run(
        {"path": "sample.txt", "start": 2, "end": 3}, context(tmp_path)
    )

    assert result.ok
    assert result.output == "2: two\n3: three"


def test_read_large_file_returns_head_and_outline(tmp_path):
    lines = ["# Title", "", "def first():", "    pass"]
    lines.extend(f"value_{number} = {number}" for number in range(100))
    lines.extend(["class Last:", "    pass", "secret_tail = True"])
    (tmp_path / "large.py").write_text("\n".join(lines) + "\n")

    result = ReadTool().run(
        {"path": "large.py"}, context(tmp_path, read_budget=120)
    )

    assert result.ok
    assert "large.py: 107 lines (overview)" in result.output
    assert "3: def first():" in result.output
    assert "105: class Last:" in result.output
    assert "107: secret_tail = True" not in result.output


def test_read_refuses_binary_and_clips_the_reason(tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"hello\0world")
    ctx = context(tmp_path)

    result = ReadTool().run({"path": "blob.bin"}, ctx)

    assert not result.ok
    assert result.output == "binary file refused: blob.bin"
    assert ctx.ledger.tool_calls == 1


def test_read_caps_a_single_generated_line_but_keeps_its_address(tmp_path):
    (tmp_path / "minified.js").write_text("const payload='" + "x" * 20_000 + "';\n")

    result = ReadTool().run(
        {"path": "minified.js", "start": 1, "end": 1}, context(tmp_path)
    )

    assert result.ok
    assert result.output.startswith("1: const payload=")
    assert "chars omitted" in result.output
    assert len(result.output) < 550


def test_list_is_sorted_gitignore_aware_and_depth_limited(tmp_path):
    (tmp_path / ".gitignore").write_text("ignored.txt\ncache/\n")
    (tmp_path / "z.txt").write_text("")
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "ignored.txt").write_text("")
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "item").write_text("")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "visible.py").write_text("")
    (tmp_path / "src" / "deep").mkdir()
    (tmp_path / "src" / "deep" / "hidden_by_depth.py").write_text("")

    result = ListTool().run({"depth": 2}, context(tmp_path))

    assert result.ok
    assert "ignored.txt" not in result.output
    assert "cache/" not in result.output
    assert "hidden_by_depth.py" not in result.output
    assert result.output.index("a.txt") < result.output.index("src/")
    assert result.output.index("src/") < result.output.index("z.txt")
    assert "    visible.py" in result.output


def test_list_collapses_large_directories_to_counts(tmp_path):
    crowded = tmp_path / "crowded"
    crowded.mkdir()
    for number in range(101):
        (crowded / f"{number:03}.txt").write_text("")

    result = ListTool().run({"depth": 2}, context(tmp_path))

    assert result.ok
    assert "crowded/" in result.output
    assert "… 101 files" in result.output
    assert "000.txt" not in result.output


def test_symbol_map_ranks_cross_file_definitions_and_accepts_query(tmp_path):
    (tmp_path / "core.py").write_text(
        "def central_service():\n    return 1\n\ndef side_path():\n    return 2\n"
    )
    for number in range(4):
        (tmp_path / f"caller_{number}.py").write_text(
            "from core import central_service\n\n"
            f"def caller_{number}():\n    return central_service()\n"
        )
    (tmp_path / "rare.py").write_text(
        "def rare_manager():\n    return 2\n\n"
        "def ultraviolet_wombat():\n    return rare_manager()\n"
    )
    ctx = context(tmp_path, read_budget=300)

    broad = ListTool().run({"symbols": True}, ctx)
    focused = ListTool().run(
        {"symbols": True, "query": "ultraviolet wombat"}, ctx
    )

    assert broad.ok
    assert broad.meta["map"] is True
    assert "core.py\n  1: def central_service():" in broad.output
    assert broad.output.index("core.py") < broad.output.index("rare.py")
    assert focused.output.index("rare.py") < focused.output.index("core.py")
    assert focused.output.index("ultraviolet_wombat") < focused.output.index(
        "rare_manager"
    )
    assert focused.meta["symbols"] > 0


def test_symbol_map_prefits_small_budget_without_ledger_clipping(tmp_path):
    for number in range(40):
        (tmp_path / f"module_{number:02}.py").write_text(
            f"def bounded_symbol_{number:02}():\n    return {number}\n"
        )
    ctx = ToolContext(tmp_path, Ledger(Budget(default=80)))

    result = ListTool().run({"symbols": True}, ctx)

    assert result.ok
    assert not ctx.ledger.drops
    assert result.meta["omitted_files"] > 0
    assert len(result.output) <= 80 * 4


def test_symbol_map_direct_renderer_obeys_tiny_character_budgets(tmp_path):
    (tmp_path / "module.py").write_text(
        "def a_very_long_symbol_name_for_budget_testing():\n    return 1\n"
    )
    index = build_index(tmp_path)

    for budget in range(100):
        repo_map = render_repo_map(index, character_budget=budget)
        assert len(repo_map.output) <= budget
