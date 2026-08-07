"""Parallel, bounded model delegation."""

from __future__ import annotations

from ..model import ModelClient
from ..subagents import SubagentRunner, advance_plan_for_tasks, validate_tasks
from .base import ToolContext, ToolResult


class DelegateTool:
    name = "delegate"
    requires_approval = True
    description = "Run up to four bounded reviewer/worker agents in parallel."
    parameters = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "role": {"type": "string", "enum": ["reviewer", "worker"]},
                        "plan_step": {
                            "type": "string",
                            "description": "Optional update_plan step label to mark completed on success.",
                        },
                    },
                    "required": ["prompt"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["tasks"],
        "additionalProperties": False,
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            tasks = validate_tasks(args.get("tasks"))
        except ValueError as exc:
            return self._result(ctx, str(exc), ok=False)
        runner = ctx.state.get("subagent-runner")
        if runner is None:
            client = ctx.state.get("model-client")
            if not isinstance(client, ModelClient):
                return self._result(ctx, "delegate is not bound to a model client", ok=False)
            runner = SubagentRunner(client)
        try:
            results = runner.run(tasks, ctx.cwd)
        except Exception as exc:
            return self._result(ctx, f"delegation failed: {type(exc).__name__}: {exc}", ok=False)
        advanced = advance_plan_for_tasks(ctx.state.get("plan"), tasks, results)
        if advanced is not None:
            ctx.state["plan"] = advanced
        body = "\n\n".join(result.render() for result in results)
        client = ctx.state.get("model-client")
        if isinstance(client, ModelClient):
            for result in results:
                client.usage.merge(result.usage)
        ok = bool(results) and all(result.ok for result in results)
        meta = {
            "tasks": len(results),
            "commits": [result.commit for result in results if result.commit],
            "sessions": [
                result.session_id for result in results if result.session_id
            ],
        }
        if advanced is not None:
            meta["plan"] = advanced
        return self._result(ctx, body, ok=ok, **meta)

    @staticmethod
    def _result(ctx: ToolContext, text: str, *, ok: bool, **meta) -> ToolResult:
        return ToolResult(
            ctx.ledger.clip("delegate", text, hint="delegate fewer or narrower tasks"),
            ok=ok,
            meta=meta,
        )


TOOLS = [DelegateTool()]
