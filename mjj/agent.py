"""The turn loop.

One rule governs the shape of this file: **the conversation is append-only and
we resend it verbatim**. Reasoning items go back exactly as they arrived
(encrypted content included), tool outputs go back clipped once and never
re-clipped, and nothing is rewritten between turns. That is what makes the
prompt cache hit, and the cache is where the token savings actually live.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from .ledger import Ledger
from .media import ImageAttachment
from .model import Event, ModelClient
from .prompt import SYSTEM_PROMPT
from .project_docs import (
    DEFAULT_MAX_BYTES,
    ProjectInstructions,
    compose,
    load as load_project_docs,
)
from .session import Session, prune_to_latest_compaction
from .tools.base import Registry, ToolContext, ToolResult


@dataclass
class Step:
    """What the caller sees while a turn runs."""

    kind: str  # reasoning | text | tool_call | tool_result | compaction | autonomous | usage | error
    text: str = ""
    name: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class Agent:
    registry: Registry
    client: ModelClient = field(default_factory=ModelClient)
    cwd: Path = field(default_factory=Path.cwd)
    ledger: Ledger = field(default_factory=Ledger)
    session: Session | None = None
    instructions: str | None = None
    project_doc_max_bytes: int = DEFAULT_MAX_BYTES
    project_instructions: ProjectInstructions = field(
        init=False, default_factory=ProjectInstructions
    )
    max_steps: int = 200
    approve: Callable[[str, dict], bool] | None = None
    items: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.instructions is None:
            self.project_instructions = load_project_docs(
                self.cwd, self.project_doc_max_bytes
            )
            self.instructions = compose(SYSTEM_PROMPT, self.project_instructions)
        self.ctx = ToolContext(cwd=self.cwd, ledger=self.ledger, approve=self.approve)
        if self.session and not self.client.cache_key:
            self.client.cache_key = f"mjj-{self.session.id}"

    # -- conversation -------------------------------------------------------

    def user(self, text: str, images: tuple[ImageAttachment, ...] = ()) -> None:
        if images:
            manifest = "\n".join(
                f'- path="{image.path}" width={image.width} height={image.height} '
                f'webp_bytes={image.encoded_bytes}'
                for image in images
            )
            text += (
                "\n\n<attached_images>\n"
                + manifest
                + "\n</attached_images>\n"
                "The images above are also available to vision. Their source paths "
                "may be read or copied with tools when the task needs the bytes."
            )
        content = [{"type": "input_text", "text": text}]
        content.extend(image.response_part() for image in images)
        self.append(
            {
                "type": "message",
                "role": "user",
                "content": content,
            }
        )

    def append(self, item: dict) -> None:
        self.items.append(item)
        if self.session:
            self.session.record(item)

    # -- the loop -----------------------------------------------------------

    def run(
        self,
        prompt: str | None = None,
        images: tuple[ImageAttachment, ...] = (),
        *,
        auto_next_steps: bool = False,
        auto_next_idea: bool = False,
        max_autonomous_turns: int = 0,
    ) -> Iterator[Step]:
        if prompt or images:
            self.user(prompt or "Describe and inspect the attached image.", images)
        autonomous_turns = 0
        tool_rounds = 0
        while tool_rounds < self.max_steps:
            calls: list[dict] = []
            response_start = len(self.items)
            emitted_text = False
            started = time.monotonic()
            try:
                for event in self.client.stream(
                    self.items,
                    self.instructions,
                    self.registry.schemas(),
                ):
                    step = self._consume(event, calls)
                    if step is not None:
                        emitted_text = emitted_text or step.kind == "text"
                        yield step
            except Exception as exc:
                yield Step(kind="error", text=f"{type(exc).__name__}: {exc}")
                return
            yield Step(
                kind="usage",
                text=self.client.usage.summary(),
                meta={"seconds": round(time.monotonic() - started, 2)},
            )
            if not calls:
                if not emitted_text:
                    final_text = _latest_assistant_text(self.items[response_start:])
                    if final_text:
                        yield Step(kind="text", text=final_text)
                    else:
                        yield Step(
                            kind="error",
                            text="model completed without an assistant message",
                        )
                        return
                autonomous = auto_next_steps or auto_next_idea
                under_limit = (
                    max_autonomous_turns == 0
                    or autonomous_turns < max_autonomous_turns
                )
                if autonomous and under_limit:
                    autonomous_turns += 1
                    follow_up = _autonomous_prompt(auto_next_steps, auto_next_idea)
                    self.user(follow_up)
                    yield Step(
                        kind="autonomous",
                        text=follow_up,
                        meta={"turn": autonomous_turns},
                    )
                    tool_rounds = 0
                    continue
                return
            for call in calls:
                yield from self._invoke(call)
            tool_rounds += 1
        yield Step(kind="error", text=f"agent exceeded {self.max_steps} tool rounds")

    def _consume(self, event: Event, calls: list[dict]) -> Step | None:
        kind = event.type
        if kind == "response.reasoning_summary_text.delta":
            return Step(kind="reasoning", text=event.delta)
        if kind == "response.output_text.delta":
            return Step(kind="text", text=event.delta)
        if kind != "response.output_item.done":
            return None
        item = event.item
        item_type = item.get("type")
        if item_type == "compaction":
            self.append(item)
            self.items, dropped = prune_to_latest_compaction(self.items)
            if self.session:
                self.session.note(compaction=True, dropped_items=dropped)
            return Step(kind="compaction", meta={"dropped_items": dropped})
        if item_type == "reasoning":
            # Verbatim, including encrypted_content. Rewriting or dropping this
            # makes the model re-think work it already did.
            self.append(item)
            return None
        if item_type == "message":
            self.append(item)
            return None
        if item_type == "function_call":
            self.append(item)
            calls.append(item)
            return Step(
                kind="tool_call",
                name=item.get("name", ""),
                text=item.get("arguments", ""),
            )
        # Anything else (web_search_call, custom tool types) is still part of
        # the transcript the model expects to see next turn.
        self.append(item)
        return None

    def _invoke(self, call: dict) -> Iterator[Step]:
        name = call.get("name", "")
        result: ToolResult = self.registry.dispatch(
            name, call.get("arguments", "") or "{}", self.ctx
        )
        output = {
            "type": "function_call_output",
            "call_id": call.get("call_id", ""),
            "output": result.output,
        }
        if call.get("caller"):
            output["caller"] = call["caller"]
        self.append(output)
        yield Step(
            kind="tool_result",
            name=name,
            text=result.output,
            meta={"ok": result.ok, **result.meta},
        )


def render(steps: Iterator[Step], out, verbose: bool = False) -> int:
    """Plain-text rendering for ``mjj exec``. Returns a process exit code."""
    failed = False
    for step in steps:
        if step.kind == "text":
            out.write(step.text)
        elif step.kind == "reasoning" and verbose:
            out.write(step.text)
        elif step.kind == "tool_call":
            label = tool_progress(step, verbose=verbose)
            out.write(f"\n· {label}\n")
        elif step.kind == "tool_result":
            body = step.text if verbose else _first_lines(step.text, 3)
            marker = "" if step.meta.get("ok", True) else " (failed)"
            out.write(f"{body}{marker}\n")
        elif step.kind == "usage" and verbose:
            out.write(f"\n[{step.text}]\n")
        elif step.kind == "compaction" and verbose:
            out.write(f"\n[compacted {step.meta.get('dropped_items', 0)} items]\n")
        elif step.kind == "autonomous":
            out.write(f"\n↻ autonomous continuation {step.meta.get('turn', 0)}\n")
        elif step.kind == "error":
            failed = True
            out.write(f"\nerror: {step.text}\n")
        out.flush()
    out.write("\n")
    return 1 if failed else 0


def render_exec(
    steps: Iterator[Step], out, err, *, verbose: bool = False, jsonl: bool = False
) -> tuple[int, str]:
    """Render a headless run with final text isolated from progress output."""
    failed = False
    response_text: list[str] = []
    response_called_tool = False
    final_text = ""
    for step in steps:
        if jsonl:
            event = {"type": step.kind}
            if step.name:
                event["name"] = step.name
            if step.text:
                event["text"] = step.text
            if step.meta:
                event["meta"] = step.meta
            out.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            out.flush()
        if step.kind == "text":
            response_text.append(step.text)
        elif step.kind == "reasoning" and verbose and not jsonl:
            err.write(step.text)
            err.flush()
        elif step.kind == "tool_call":
            response_called_tool = True
            if not jsonl:
                err.write(f"· {tool_progress(step, verbose=verbose)}\n")
                err.flush()
        elif step.kind == "tool_result" and verbose and not jsonl:
            marker = "" if step.meta.get("ok", True) else " (failed)"
            err.write(f"{step.text}{marker}\n")
            err.flush()
        elif step.kind == "usage":
            if not response_called_tool:
                final_text = "".join(response_text)
            if verbose and not jsonl:
                err.write(f"[{step.text}]\n")
                err.flush()
            response_text.clear()
            response_called_tool = False
        elif step.kind == "compaction" and verbose and not jsonl:
            err.write(f"[compacted {step.meta.get('dropped_items', 0)} items]\n")
            err.flush()
        elif step.kind == "autonomous" and not jsonl:
            err.write(f"↻ autonomous continuation {step.meta.get('turn', 0)}\n")
            err.flush()
        elif step.kind == "error":
            failed = True
            if not jsonl:
                err.write(f"error: {step.text}\n")
                err.flush()
    if response_text and not response_called_tool:
        final_text = "".join(response_text)
    if not jsonl and final_text:
        out.write(final_text)
        if not final_text.endswith("\n"):
            out.write("\n")
        out.flush()
    return (1 if failed else 0), final_text


def _first_lines(text: str, count: int) -> str:
    lines = text.splitlines()
    if len(lines) <= count:
        return text
    return "\n".join(lines[:count]) + f"\n  … {len(lines) - count} more lines"


def tool_progress(step: Step, *, verbose: bool = False) -> str:
    """Turn wire-level tool JSON into progress a human can scan."""
    try:
        args = json.loads(step.text or "{}")
    except ValueError:
        args = {}
    if step.name == "skill":
        return (
            f"loading workflow {args['name']}"
            if args.get("name")
            else "checking available workflows"
        )
    if step.name == "apply_patch":
        return "editing files"
    if step.name == "shell":
        command = args.get("command", "")
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        return f"running {str(command)[:180]}"
    if step.name == "read":
        return f"reading {args.get('path', '')}".rstrip()
    if step.name == "list":
        return f"listing {args.get('path', '.')}"
    if step.name == "search":
        return f"searching for {args.get('query', '')}".rstrip()
    rendered = step.text if verbose else step.text[:120]
    suffix = "…" if len(step.text) > len(rendered) else ""
    return f"{step.name} {rendered}{suffix}".rstrip()


def _latest_assistant_text(items: list[dict]) -> str:
    for item in reversed(items):
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        return "".join(
            part.get("text", "")
            for part in item.get("content", [])
            if part.get("type") in ("output_text", "text")
        )
    return ""


def _autonomous_prompt(next_steps: bool, next_idea: bool) -> str:
    instructions = [
        "You are in AUTONOMOUS MODE. Do not ask for permission or confirmation."
    ]
    if next_steps:
        instructions.append(
            "Continue the current objective through its next concrete steps. "
            "Execute them in order and run relevant checks."
        )
    if next_idea:
        instructions.append(
            "When the current objective is genuinely complete, identify three useful "
            "improvements, choose the highest-impact one, and begin implementing it."
        )
    if next_steps and next_idea:
        instructions.append("Repeat this cycle until a human interrupts you.")
    return "\n\n".join(instructions)
