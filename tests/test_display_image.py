from __future__ import annotations

import json

from PIL import Image

from mjj.ledger import Ledger, estimate_tokens
from mjj.tools import build_registry
from mjj.tools.base import ToolContext


def test_display_image_returns_metadata_event_without_pixel_bytes(tmp_path) -> None:
    image = tmp_path / "art" / "water.webp"
    image.parent.mkdir()
    Image.new("RGB", (96, 48), "teal").save(image, format="WEBP", quality=85)
    registry = build_registry(only=["display"])
    ledger = Ledger()

    result = registry.dispatch(
        "display_image",
        json.dumps({"path": "art/water.webp"}),
        ToolContext(tmp_path, ledger),
    )

    assert result.ok
    assert result.meta["terminal_image"] == "art/water.webp"
    assert (result.meta["width"], result.meta["height"]) == (96, 48)
    assert result.meta["format"] == "WEBP"
    assert "base64" not in result.output
    assert str(tmp_path) not in result.output
    assert ledger.tool_calls == 1


def test_display_image_rejects_workspace_escape_symlink_and_corrupt_file(tmp_path) -> None:
    outside = tmp_path.parent / "outside-display.png"
    Image.new("RGB", (4, 4), "red").save(outside)
    linked = tmp_path / "linked.png"
    linked.symlink_to(outside)
    corrupt = tmp_path / "broken.png"
    corrupt.write_bytes(b"not an image")
    registry = build_registry(only=["display"])
    ctx = ToolContext(tmp_path, Ledger())

    escaped = registry.dispatch(
        "display_image", json.dumps({"path": str(outside)}), ctx
    )
    symlink = registry.dispatch(
        "display_image", json.dumps({"path": "linked.png"}), ctx
    )
    broken = registry.dispatch(
        "display_image", json.dumps({"path": "broken.png"}), ctx
    )

    assert not escaped.ok and "workspace" in escaped.output
    assert not symlink.ok and "symlink" in symlink.output
    assert not broken.ok and "corrupt" in broken.output


def test_display_image_schema_has_small_always_on_cost() -> None:
    schemas = {item["name"]: item for item in build_registry().schemas()}
    encoded = json.dumps(schemas["display_image"], separators=(",", ":"))
    assert estimate_tokens(encoded) <= 70
