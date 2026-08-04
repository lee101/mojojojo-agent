from __future__ import annotations

import json

from mjj.ledger import Ledger
from mjj.tools import build_registry
from mjj.tools.base import ToolContext


def test_update_plan_keeps_compact_structured_state(tmp_path):
    registry = build_registry(only=["plan"])
    context = ToolContext(tmp_path, Ledger())

    result = registry.dispatch(
        "update_plan",
        json.dumps(
            {
                "explanation": "Starting implementation",
                "plan": [
                    {"step": "Inspect", "status": "completed"},
                    {"step": "Implement", "status": "in_progress"},
                    {"step": "Verify", "status": "pending"},
                ],
            }
        ),
        context,
    )

    assert result.ok
    assert json.loads(result.output) == {
        "steps": 3,
        "pending": 1,
        "in_progress": 1,
        "completed": 1,
    }
    assert context.state["plan"]["plan"][1]["step"] == "Implement"


def test_update_plan_rejects_multiple_steps_in_progress(tmp_path):
    registry = build_registry(only=["plan"])
    context = ToolContext(tmp_path, Ledger())
    plan = [
        {"step": "One", "status": "in_progress"},
        {"step": "Two", "status": "in_progress"},
    ]

    result = registry.dispatch("update_plan", json.dumps({"plan": plan}), context)

    assert not result.ok
    assert "at most one" in result.output
