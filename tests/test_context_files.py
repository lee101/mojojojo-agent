from __future__ import annotations

from PIL import Image
import pytest

from mjj.context_files import FileMentionError, discover_project_files, prepare_mentions


def test_file_mentions_attach_text_and_line_ranges_once(tmp_path) -> None:
    source = tmp_path / "src" / "sample.py"
    source.parent.mkdir()
    source.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    bundle = prepare_mentions(
        "Review @src/sample.py:2-3 and @src/sample.py:2-3", tmp_path
    )

    assert bundle.files == (source,)
    assert 'path="src/sample.py" lines=2-3' in bundle.text
    assert "two\nthree" in bundle.text
    assert bundle.text.count("<file ") == 1


def test_quoted_file_mentions_and_text_budget_are_supported(tmp_path) -> None:
    source = tmp_path / "notes with spaces.txt"
    source.write_text("abcdefghij", encoding="utf-8")

    bundle = prepare_mentions('Use @"notes with spaces.txt"', tmp_path, max_file_bytes=4)

    assert "abcd" in bundle.text
    assert 'truncated="true"' in bundle.text
    assert "efghij" not in bundle.text


def test_image_mentions_use_bounded_vision_attachment_path(tmp_path) -> None:
    source = tmp_path / "reference.png"
    Image.new("RGB", (12, 8), "navy").save(source)

    bundle = prepare_mentions("Match @reference.png", tmp_path)

    assert bundle.files == (source,)
    assert len(bundle.images) == 1
    assert bundle.images[0].data_url.startswith("data:image/webp;base64,")
    assert "<attached_files>" not in bundle.text


def test_missing_and_binary_mentions_fail_clearly(tmp_path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"a\0b")

    with pytest.raises(FileMentionError, match="does not exist"):
        prepare_mentions("Read @missing.py", tmp_path)
    with pytest.raises(FileMentionError, match="binary"):
        prepare_mentions("Read @blob.bin", tmp_path)


def test_non_file_at_words_remain_ordinary_prompt_text(tmp_path) -> None:
    prompt = " ".join(f"@decorator{index}" for index in range(20))
    bundle = prepare_mentions(prompt, tmp_path)

    assert bundle.text == prompt
    assert bundle.files == ()


def test_project_file_discovery_is_bounded_and_skips_vendor_dirs(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "secret").write_text("", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("", encoding="utf-8")

    assert discover_project_files(tmp_path) == ("src/main.py",)
