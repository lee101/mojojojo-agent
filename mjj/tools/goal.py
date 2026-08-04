"""On-demand goal control tool, installed only while a goal is active."""

from __future__ import annotations

import json

from ..goals import GoalStore
from .base import ToolContext, ToolResult


class GoalTool:
    name = "goal"
    description = "Inspect or checkpoint the active goal; complete/block only with evidence"
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "progress", "complete", "blocked"],
            },
            "message": {"type": "string"},
            "evidence": {"type": "string"},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        store = ctx.state.get("goal-store")
        if not isinstance(store, GoalStore):
            return ToolResult.error("no goal store is bound to this run")
        action = str(args.get("action") or "")
        message = str(args.get("message") or "")
        evidence = str(args.get("evidence") or "")
        try:
            if action == "status":
                goal = store.load()
                if goal is None:
                    return ToolResult.error("no active goal")
            elif action == "progress":
                goal = store.record(message, evidence=evidence)
            elif action in ("complete", "blocked"):
                if not message.strip():
                    return ToolResult.error(
                        f"goal {action} requires a concise evidence-backed message"
                    )
                goal = store.transition(action, message)
            else:
                return ToolResult.error(
                    "action must be status, progress, complete, or blocked"
                )
        except ValueError as exc:
            return ToolResult.error(str(exc))
        public = {
            "id": goal.id,
            "status": goal.status,
            "objective": goal.objective,
            "checkpoints": len(goal.progress),
        }
        if goal.progress:
            public["latest"] = goal.progress[-1]
        return ToolResult(
            output=ctx.ledger.clip("goal", json.dumps(public, ensure_ascii=False)),
            meta={"goal_status": goal.status, "goal_id": goal.id},
        )


TOOLS = [GoalTool()]
