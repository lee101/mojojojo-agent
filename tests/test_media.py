from __future__ import annotations

from PIL import Image

from mjj import media
from mjj.media import MAX_EDGE, ImageInputError, inspect_image, prepare_image
from mjj.agent import Agent
from mjj.ledger import Ledger
from mjj.tools.base import Registry


def test_image_is_resized_and_precompressed_to_quality_85_webp(tmp_path):
    source = tmp_path / "large.png"
    Image.new("RGB", (3000, 1500), (12, 120, 220)).save(source)
    attachment = prepare_image(source)
    assert attachment.data_url.startswith("data:image/webp;base64,")
    assert (attachment.width, attachment.height) == (MAX_EDGE, MAX_EDGE // 2)
    assert attachment.encoded_bytes < attachment.original_bytes
    assert attachment.response_part()["detail"] == "auto"


def test_agent_turn_exposes_attachment_path_without_putting_bytes_in_text(tmp_path):
    source = tmp_path / "reference.png"
    Image.new("RGB", (12, 8), "purple").save(source)
    attachment = prepare_image(source)
    agent = Agent(registry=Registry(), ledger=Ledger(), instructions="test")
    agent.user("transform this", (attachment,))
    text = agent.items[0]["content"][0]["text"]
    assert str(source) in text
    assert "width=12 height=8" in text
    assert "base64" not in text
    assert agent.items[0]["content"][1]["image_url"].startswith("data:image/webp")


def test_image_metadata_inspection_does_not_encode_pixels(tmp_path):
    source = tmp_path / "reference.png"
    Image.new("RGBA", (20, 10), "orange").save(source)

    info = inspect_image(source)

    assert (info.width, info.height, info.format) == (20, 10, "PNG")
    assert info.bytes == source.stat().st_size


def test_stdlib_image_path_reads_and_attaches_bounded_png(tmp_path, monkeypatch):
    source = tmp_path / "small.png"
    Image.new("RGB", (31, 17), "teal").save(source)
    monkeypatch.setattr(media, "_pillow", lambda: None)

    info = inspect_image(source)
    attachment = prepare_image(source)

    assert (info.width, info.height, info.format) == (31, 17, "PNG")
    assert attachment.data_url.startswith("data:image/png;base64,")
    assert attachment.encoded_bytes == source.stat().st_size
    assert "PNG" in attachment.summary()


def test_stdlib_header_reader_supports_model_image_formats(tmp_path, monkeypatch):
    expected = {"JPEG": "JPEG", "GIF": "GIF", "WEBP": "WEBP"}
    sources = []
    formats = (("jpg", "JPEG"), ("gif", "GIF"), ("webp", "WEBP"))
    for extension, image_format in formats:
        source = tmp_path / f"small.{extension}"
        Image.new("RGB", (37, 19), "orange").save(source, format=image_format)
        sources.append((source, expected[image_format]))
    monkeypatch.setattr(media, "_pillow", lambda: None)

    for source, image_format in sources:
        info = inspect_image(source)
        assert (info.width, info.height, info.format) == (37, 19, image_format)


def test_stdlib_image_path_rejects_input_that_needs_resizing(tmp_path, monkeypatch):
    source = tmp_path / "large.png"
    Image.new("RGB", (MAX_EDGE + 1, 2), "navy").save(source)
    monkeypatch.setattr(media, "_pillow", lambda: None)

    try:
        prepare_image(source)
    except ImageInputError as exc:
        assert "[vision]" in str(exc)
    else:
        raise AssertionError("oversized dependency-free image was accepted")
