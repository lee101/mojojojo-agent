"""Compact structured plan state, compatible with the common update_plan shape."""

from __future__ import annotations

import json

from .base import ToolContext, ToolResult


STATUSES = ("pending", "in_progress", "completed")
MAX_STEPS = 20
MAX_STEP_CHARS = 500


class UpdatePlanTool:
    name = "update_plan"
    description = "Set the current multi-step task plan; keep exactly one step in progress"
    parameters = {
        "type": "object",
        "properties": {
            "explanation": {"type": "string"},
            "plan": {
                "type": "array",
                "maxItems": MAX_STEPS,
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "string"},
                        "status": {"type": "string", "enum": list(STATUSES)},
                    },
                    "required": ["step", "status"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["plan"],
        "additionalProperties": False,
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        raw_plan = args.get("plan")
        if not isinstance(raw_plan, list) or len(raw_plan) > MAX_STEPS:
            return ToolResult.error(f"plan must be an array of at most {MAX_STEPS} steps")
        plan: list[dict[str, str]] = []
        for item in raw_plan:
            if not isinstance(item, dict):
                return ToolResult.error("each plan item must be an object")
            step = item.get("step")
            status = item.get("status")
            if not isinstance(step, str) or not step.strip():
                return ToolResult.error("each plan step must be non-empty")
            if len(step) > MAX_STEP_CHARS:
                return ToolResult.error(
                    f"plan steps must not exceed {MAX_STEP_CHARS} characters"
                )
            if status not in STATUSES:
                return ToolResult.error("plan status must be pending, in_progress, or completed")
            plan.append({"step": step.strip(), "status": status})
        if sum(item["status"] == "in_progress" for item in plan) > 1:
            return ToolResult.error("at most one plan step may be in_progress")
        explanation = args.get("explanation", "")
        if not isinstance(explanation, str):
            return ToolResult.error("explanation must be a string")
        state = {"explanation": explanation.strip()[:1000], "plan": plan}
        ctx.state["plan"] = state
        counts = {status: 0 for status in STATUSES}
        for item in plan:
            counts[item["status"]] += 1
        summary = {
            "steps": len(plan),
            "pending": counts["pending"],
            "in_progress": counts["in_progress"],
            "completed": counts["completed"],
        }
        return ToolResult(
            output=ctx.ledger.clip("update_plan", json.dumps(summary, separators=(",", ":"))),
            meta={"plan": state},
        )


TOOLS = [UpdatePlanTool()]
