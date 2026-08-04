"""List and undo automatic patch checkpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from ..checkpoints import CheckpointError, store_for
from .base import ToolContext, ToolResult


class CheckpointTool:
    name = "checkpoint"
    description = "List patch checkpoints or safely undo one."
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "undo"]},
            "id": {"type": "string", "description": "Checkpoint id; undo defaults latest."},
        },
        "additionalProperties": False,
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        action = args.get("action", "list")
        if action not in {"list", "undo"}:
            return self._result(ctx, "action must be list or undo", ok=False)
        identifier = args.get("id")
        if identifier is not None and (not isinstance(identifier, str) or not identifier):
            return self._result(ctx, "id must be a non-empty string", ok=False)
        try:
            store = store_for(ctx.cwd, ctx.state)
            if action == "list":
                checkpoints = store.list()[:20]
                if not checkpoints:
                    return self._result(ctx, "no checkpoints", checkpoints=0)
                lines = []
                for item in checkpoints:
                    instant = datetime.fromtimestamp(item.created, timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                    state = " · undone" if item.undone else ""
                    lines.append(
                        f"{item.identifier} · {instant} · {item.files} files · "
                        f"{item.bytes} bytes{state}"
                    )
                return self._result(ctx, "\n".join(lines), checkpoints=len(lines))
            if ctx.approve is not None:
                try:
                    approved = ctx.approve(
                        "checkpoint", {"action": "undo", "id": identifier or "latest"}
                    )
                except Exception as exc:
                    return self._result(ctx, f"approval failed: {exc}", ok=False)
                if not approved:
                    return self._result(ctx, "checkpoint undo denied", ok=False, denied=True)
            restored = store.undo(identifier)
            return self._result(
                ctx,
                f"checkpoint {restored.identifier} restored · {restored.files} files",
                checkpoint=restored.identifier,
                files=restored.files,
            )
        except (OSError, CheckpointError) as exc:
            return self._result(ctx, f"checkpoint: {exc}", ok=False)

    @staticmethod
    def _result(ctx: ToolContext, text: str, *, ok: bool = True, **meta) -> ToolResult:
        return ToolResult(ctx.ledger.clip("checkpoint", text), ok=ok, meta=meta)


TOOLS = [CheckpointTool()]
