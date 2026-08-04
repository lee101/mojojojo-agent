"""Image display event for interactive clients."""

from __future__ import annotations

from pathlib import Path

from ..media import ImageInputError, inspect_image
from .base import ToolContext, ToolResult


class DisplayImageTool:
    name = "display_image"
    description = "Show a workspace image in the interactive terminal."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        value = args.get("path")
        if not isinstance(value, str) or not value.strip():
            return self._error(ctx, "path must be a non-empty string")
        unresolved = Path(value).expanduser()
        candidate = (
            unresolved if unresolved.is_absolute() else ctx.cwd / unresolved
        )
        if candidate.is_symlink():
            return self._error(ctx, "image path must not be a symlink")
        path = candidate.resolve()
        try:
            relative = path.relative_to(ctx.cwd).as_posix()
        except ValueError:
            return self._error(ctx, "image path must stay inside the workspace")
        try:
            info = inspect_image(path)
        except ImageInputError as exc:
            return self._error(ctx, str(exc))
        output = ctx.ledger.clip("display_image", info.summary(name=relative))
        return ToolResult(
            output,
            meta={
                "terminal_image": relative,
                "width": info.width,
                "height": info.height,
                "format": info.format,
                "bytes": info.bytes,
                "frames": info.frames,
            },
        )

    @staticmethod
    def _error(ctx: ToolContext, message: str) -> ToolResult:
        return ToolResult.error(ctx.ledger.clip("display_image", message))


TOOLS = [DisplayImageTool()]
