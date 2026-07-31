from pathlib import Path

from mjj.ledger import Ledger
from mjj.tools.base import ToolContext
from mjj.tools.patch import ApplyPatchTool


def apply(tmp_path: Path, patch: str):
    ctx = ToolContext(tmp_path, Ledger())
    return ApplyPatchTool().run({"input": patch}, ctx), ctx


def test_add_update_and_delete_files(tmp_path):
    (tmp_path / "old.txt").write_text("remove me\n")
    (tmp_path / "edit.txt").write_text("before\nold\nafter\n")
    patch = """*** Begin Patch
*** Add File: nested/new.txt
+first
+second
*** Update File: edit.txt
@@
 before
-old
+new
 after
*** Delete File: old.txt
*** End Patch"""

    result, ctx = apply(tmp_path, patch)

    assert result.ok
    assert (tmp_path / "nested" / "new.txt").read_text() == "first\nsecond\n"
    assert (tmp_path / "edit.txt").read_text() == "before\nnew\nafter\n"
    assert not (tmp_path / "old.txt").exists()
    assert "nested/new.txt: +2 -0" in result.output
    assert "edit.txt: +1 -1" in result.output
    assert "old.txt: +0 -1" in result.output
    assert ctx.ledger.tool_calls == 1


def test_context_and_whitespace_matching_are_tolerant(tmp_path):
    (tmp_path / "code.py").write_text(
        "class Example:\n"
        "    def one(self):\n"
        "        return 1\n"
        "\n"
        "    def two(self):\n"
        "        value = 2\n"
        "        return value\n"
    )
    patch = """*** Begin Patch
*** Update File: code.py
@@ class Example
@@ def two(self):
-      value = 2
+        value = 3
         return value
*** End Patch"""

    result, _ = apply(tmp_path, patch)

    assert result.ok
    assert "value = 3" in (tmp_path / "code.py").read_text()


def test_failed_multi_file_patch_writes_nothing(tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("original first\n")
    second.write_text("original second\n")
    patch = """*** Begin Patch
*** Update File: first.txt
@@
-original first
+changed first
*** Update File: second.txt
@@
-missing context
+changed second
*** End Patch"""

    result, _ = apply(tmp_path, patch)

    assert not result.ok
    assert "context not found" in result.output
    assert first.read_text() == "original first\n"
    assert second.read_text() == "original second\n"


def test_rejects_absolute_and_escaping_paths(tmp_path):
    absolute, _ = apply(
        tmp_path,
        f"*** Begin Patch\n*** Add File: {tmp_path / 'bad.txt'}\n+x\n*** End Patch",
    )
    escaping, _ = apply(
        tmp_path,
        "*** Begin Patch\n*** Add File: ../bad.txt\n+x\n*** End Patch",
    )

    assert not absolute.ok
    assert "absolute paths" in absolute.output
    assert not escaping.ok
    assert "escapes the workspace" in escaping.output
    assert not (tmp_path.parent / "bad.txt").exists()


def test_delete_failure_is_atomic_with_add(tmp_path):
    patch = """*** Begin Patch
*** Add File: added.txt
+new
*** Delete File: absent.txt
*** End Patch"""

    result, _ = apply(tmp_path, patch)

    assert not result.ok
    assert not (tmp_path / "added.txt").exists()
