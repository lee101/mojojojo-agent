"""Vision intake: compress a workspace image to quality-85 WebP for the model."""

from __future__ import annotations

from pathlib import Path

from ..media import WEBP_QUALITY, ImageInputError, prepare_image
from .base import ToolContext, ToolResult


class ReadImageTool:
    name = "read_image"
    description = (
        "Load a workspace image, resize it if needed, compress to quality-85 "
        "WebP, and attach it to the next model turn for vision. Use for UI "
        "screenshots, mockups, and visualbench captures before changing art."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Image path relative to the workspace (or absolute inside it).",
            },
            "note": {
                "type": "string",
                "description": "Optional short question or focus for the vision pass.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        value = args.get("path")
        if not isinstance(value, str) or not value.strip():
            return self._error(ctx, "path must be a non-empty string")
        unresolved = Path(value).expanduser()
        candidate = unresolved if unresolved.is_absolute() else ctx.cwd / unresolved
        if candidate.is_symlink():
            return self._error(ctx, "image path must not be a symlink")
        path = candidate.resolve()
        try:
            relative = path.relative_to(ctx.cwd).as_posix()
        except ValueError:
            return self._error(ctx, "image path must stay inside the workspace")
        try:
            attachment = prepare_image(path)
        except ImageInputError as exc:
            return self._error(ctx, str(exc))

        note = args.get("note")
        note_text = note.strip() if isinstance(note, str) and note.strip() else ""
        pending = ctx.state.setdefault("pending_vision", [])
        pending.append(
            {
                "attachment": attachment,
                "note": note_text,
                "tool": self.name,
                "relative": relative,
            }
        )
        summary = (
            f"{relative} · {attachment.width}×{attachment.height} · "
            f"WebP q{WEBP_QUALITY} {attachment.encoded_bytes / 1024:.0f} KiB"
            f" (from {attachment.original_bytes / 1024:.0f} KiB)"
        )
        if note_text:
            summary += f"\nfocus: {note_text}"
        return ToolResult(
            ctx.ledger.clip(self.name, summary),
            meta={
                "vision": True,
                "path": relative,
                "width": attachment.width,
                "height": attachment.height,
                "encoded_bytes": attachment.encoded_bytes,
                "original_bytes": attachment.original_bytes,
            },
        )

    @staticmethod
    def _error(ctx: ToolContext, message: str) -> ToolResult:
        return ToolResult.error(ctx.ledger.clip("read_image", message))


TOOLS = [ReadImageTool()]
