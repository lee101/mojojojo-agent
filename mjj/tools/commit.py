"""Stage run-changed files and create one git commit."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .base import ToolContext, ToolResult
from .check import _changed_names

MAX_MESSAGE = 2_000
MAX_OUTPUT = 4_000


class CommitTool:
    name = "commit"
    requires_approval = True
    description = (
        "Stage files changed this run (or explicit paths) and create one git "
        "commit. Prefer after check/verify are clean."
    )
    parameters = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Commit subject/body (required).",
            },
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 50,
                "description": "Files to stage; defaults to files changed this run.",
            },
            "all": {
                "type": "boolean",
                "description": "Stage all tracked modifications (git add -u).",
            },
        },
        "required": ["message"],
        "additionalProperties": False,
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        message = args.get("message")
        if not isinstance(message, str) or not message.strip():
            return self._result(ctx, "message must be a non-empty string", ok=False)
        if len(message) > MAX_MESSAGE:
            return self._result(
                ctx, f"message must be at most {MAX_MESSAGE} characters", ok=False
            )
        stage_all = args.get("all", False)
        if not isinstance(stage_all, bool):
            return self._result(ctx, "all must be true or false", ok=False)
        root = ctx.cwd.resolve()
        if not (root / ".git").exists():
            return self._result(ctx, "not a git repository", ok=False)

        try:
            paths = self._paths(args.get("paths"), ctx, stage_all=stage_all)
        except ValueError as exc:
            return self._result(ctx, str(exc), ok=False)

        if not stage_all and not paths:
            return self._result(
                ctx, "no changed files; pass paths or set all=true", ok=False
            )

        if stage_all:
            add = _git(root, "add", "-u")
        else:
            add = _git(root, "add", "--", *paths)
        if add.returncode:
            return self._result(
                ctx, f"git add failed: {_clip(add.stdout or add.stderr)}", ok=False
            )

        staged = _git(root, "diff", "--cached", "--name-only")
        names = [line for line in staged.stdout.splitlines() if line.strip()]
        if not names:
            return self._result(ctx, "nothing staged to commit", ok=False)

        commit = _git(
            root,
            "commit",
            "-m",
            message.strip(),
            "--",
            *names,
        )
        if commit.returncode:
            return self._result(
                ctx,
                f"git commit failed: {_clip(commit.stdout or commit.stderr)}",
                ok=False,
            )

        rev = _git(root, "rev-parse", "--short", "HEAD")
        sha = rev.stdout.strip() if rev.returncode == 0 else ""
        summary = f"commit ✓ {sha}" if sha else "commit ✓"
        summary += f" · {len(names)} file{'' if len(names) == 1 else 's'}"
        body = summary + "\n" + "\n".join(names[:50])
        return self._result(ctx, body, files=names, commit=sha)

    def _paths(self, raw, ctx: ToolContext, *, stage_all: bool) -> list[str]:
        if stage_all:
            return []
        if raw is None:
            return _changed_names(ctx)
        if not isinstance(raw, list) or any(
            not isinstance(item, str) or not item for item in raw
        ):
            raise ValueError("paths must be an array of non-empty strings")
        if len(raw) > 50:
            raise ValueError("paths may contain at most 50 files")
        root = ctx.cwd.resolve()
        names: list[str] = []
        for name in raw:
            path = ctx.resolve(name)
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError as exc:
                raise ValueError(
                    f"commit path must stay inside the workspace: {name}"
                ) from exc
            names.append(relative)
        return names

    @staticmethod
    def _result(ctx: ToolContext, text: str, *, ok: bool = True, **meta) -> ToolResult:
        return ToolResult(
            ctx.ledger.clip("commit", text, hint="narrow paths or fix git state"),
            ok=ok,
            meta=meta,
        )


def _git(root: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *argv],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def _clip(text: str) -> str:
    cleaned = " ".join(text.split())
    return cleaned[:MAX_OUTPUT] if cleaned else "unknown error"


TOOLS = [CommitTool()]
