from __future__ import annotations

from PIL import Image

from mjj.agent import Agent
from mjj.ledger import Ledger
from mjj.model import Event, ModelClient
from mjj.tools import build_registry
from mjj.tools.base import Registry, ToolContext
from mjj.tools.read_image import ReadImageTool


def test_read_image_queues_webp85_vision_attachment(tmp_path):
    source = tmp_path / "shot.png"
    Image.new("RGB", (640, 360), (40, 80, 20)).save(source)
    ctx = ToolContext(cwd=tmp_path, ledger=Ledger())
    result = ReadImageTool().run({"path": "shot.png", "note": "HUD contrast?"}, ctx)
    assert result.ok
    assert "WebP q85" in result.output
    assert "HUD contrast?" in result.output
    pending = ctx.state["pending_vision"]
    assert len(pending) == 1
    attachment = pending[0]["attachment"]
    assert attachment.data_url.startswith("data:image/webp;base64,")
    assert attachment.encoded_format == "WebP"


def test_read_image_rejects_escape(tmp_path):
    ctx = ToolContext(cwd=tmp_path, ledger=Ledger())
    result = ReadImageTool().run({"path": "../outside.png"}, ctx)
    assert not result.ok


def test_agent_injects_read_image_into_next_user_turn(tmp_path, monkeypatch):
    source = tmp_path / "ui.png"
    Image.new("RGB", (32, 24), "red").save(source)
    turns = [
        [
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "read_image",
                "arguments": '{"path":"ui.png","note":"title screen"}',
            }
        ],
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "overlay is muddy"}],
            }
        ],
    ]

    def stream(self, items, instructions, tools=None):
        for item in turns.pop(0):
            if item["type"] == "message":
                yield Event(
                    "response.output_text.delta",
                    {"delta": item["content"][0]["text"]},
                )
            yield Event("response.output_item.done", {"item": item})

    monkeypatch.setattr(ModelClient, "stream", stream)
    registry = Registry()
    registry.add(ReadImageTool())
    agent = Agent(registry=registry, cwd=tmp_path, ledger=Ledger(), instructions="test")
    steps = list(agent.run("review the title screen"))
    assert any(step.kind == "tool_result" and step.name == "read_image" for step in steps)
    vision_msgs = [
        item
        for item in agent.items
        if item.get("role") == "user"
        and any(
            part.get("type") == "input_image"
            for part in item.get("content", [])
            if isinstance(part, dict)
        )
    ]
    assert vision_msgs
    assert "title screen" in vision_msgs[-1]["content"][0]["text"]


def test_read_image_is_registered_by_default():
    registry = build_registry()
    assert "read_image" in registry.tools
