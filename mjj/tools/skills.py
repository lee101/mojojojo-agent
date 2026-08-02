"""Token-bounded discovery and loading of specialized SKILL.md instructions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..skills import discover, find
from .base import ToolContext, ToolResult


@dataclass
class SkillTool:
    include_user: bool = True
    extra_paths: tuple[Path, ...] = field(default_factory=tuple)
    name: str = "skill"
    description: str = "List available skills or load one specialized workflow by name."
    parameters: dict = field(init=False)

    def __post_init__(self) -> None:
        self.parameters = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name; omit to list available skills.",
                }
            },
            "additionalProperties": False,
        }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        requested = args.get("name")
        if requested is not None and (
            not isinstance(requested, str) or not requested.strip()
        ):
            return self._result(ctx, "name must be a non-empty string", ok=False)
        skills = discover(
            ctx.cwd,
            include_user=self.include_user,
            extra_paths=self.extra_paths,
        )
        if requested is None:
            if not skills:
                return self._result(ctx, "no skills found")
            lines = [
                f"{skill.qualified_name}: {skill.description}"
                for skill in skills
            ]
            return self._result(ctx, "\n".join(lines), skills=len(skills))

        skill, ambiguous = find(skills, requested.strip())
        if ambiguous:
            return self._result(
                ctx,
                "ambiguous skill; use one of: " + ", ".join(ambiguous),
                ok=False,
            )
        if skill is None:
            return self._result(ctx, f"unknown skill {requested!r}", ok=False)
        try:
            body = skill.body()
            files = skill.files()
        except OSError as exc:
            return self._result(ctx, f"cannot load {skill.path}: {exc}", ok=False)
        output = [
            f'<skill name="{skill.name}" path="{skill.path}">',
            body,
            "",
            f"Base directory: {skill.path.parent}",
        ]
        if files:
            output.append("Bundled files: " + ", ".join(files))
        output.append("</skill>")
        return self._result(
            ctx,
            "\n".join(output),
            skill=skill.qualified_name,
            path=str(skill.path),
            files=len(files),
        )

    @staticmethod
    def _result(ctx: ToolContext, text: str, *, ok: bool = True, **meta) -> ToolResult:
        return ToolResult(
            ctx.ledger.clip("skill", text, hint="load bundled references with read"),
            ok=ok,
            meta=meta,
        )


TOOLS = [SkillTool()]


def build_tools(*, include_user: bool, extra_paths=()) -> list[SkillTool]:
    return [
        SkillTool(
            include_user=include_user,
            extra_paths=tuple(Path(path).resolve() for path in extra_paths),
        )
    ]
