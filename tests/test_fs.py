from pathlib import Path

from mjj.ledger import Budget, Ledger
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
