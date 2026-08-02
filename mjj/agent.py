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
from .model import Event, ModelClient
from .prompt import SYSTEM_PROMPT
from .session import Session, prune_to_latest_compaction
from .tools.base import Registry, ToolContext, ToolResult


@dataclass
class Step:
    """What the caller sees while a turn runs."""

    kind: str  # reasoning | text | tool_call | tool_result | compaction | usage | error
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
    instructions: str = SYSTEM_PROMPT
    max_steps: int = 200
    approve: Callable[[str, dict], bool] | None = None
    items: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.ctx = ToolContext(cwd=self.cwd, ledger=self.ledger, approve=self.approve)
        if self.session and not self.client.cache_key:
            self.client.cache_key = f"mjj-{self.session.id}"

    # -- conversation -------------------------------------------------------

    def user(self, text: str) -> None:
        self.append(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            }
        )

    def append(self, item: dict) -> None:
        self.items.append(item)
        if self.session:
            self.session.record(item)

    # -- the loop -----------------------------------------------------------

    def run(self, prompt: str | None = None) -> Iterator[Step]:
        if prompt:
            self.user(prompt)
        for _ in range(self.max_steps):
            calls: list[dict] = []
            started = time.monotonic()
            try:
                for event in self.client.stream(
                    self.items,
                    self.instructions,
                    self.registry.schemas(),
                ):
                    step = self._consume(event, calls)
                    if step is not None:
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
                return
            for call in calls:
                yield from self._invoke(call)

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
            args = step.text
            if len(args) > 160 and not verbose:
                args = args[:160] + "…"
            out.write(f"\n· {step.name} {args}\n")
        elif step.kind == "tool_result":
            body = step.text if verbose else _first_lines(step.text, 3)
            marker = "" if step.meta.get("ok", True) else " (failed)"
            out.write(f"{body}{marker}\n")
        elif step.kind == "usage" and verbose:
            out.write(f"\n[{step.text}]\n")
        elif step.kind == "compaction" and verbose:
            out.write(f"\n[compacted {step.meta.get('dropped_items', 0)} items]\n")
        elif step.kind == "error":
            failed = True
            out.write(f"\nerror: {step.text}\n")
        out.flush()
    out.write("\n")
    return 1 if failed else 0


def _first_lines(text: str, count: int) -> str:
    lines = text.splitlines()
    if len(lines) <= count:
        return text
    return "\n".join(lines[:count]) + f"\n  … {len(lines) - count} more lines"
