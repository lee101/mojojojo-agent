from __future__ import annotations

from PIL import Image

from mjj.media import MAX_EDGE, prepare_image
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
