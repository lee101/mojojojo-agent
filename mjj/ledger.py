"""Token accounting and the one truncation policy every tool obeys.

The rule that makes this harness cheap: a tool result is a *summary with an
address*, never a dump. If output does not fit its budget, the middle goes and
the tool says exactly what it dropped and how to get it back. The model can
always ask for the rest; it can never be forced to pay for the rest.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# ~4 chars per token is close enough for budgeting and costs nothing. Swapping
# in a real BPE counter changes the numbers by a few percent and costs a
# dependency plus milliseconds per call.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def _clip_blob(text: str, limit: int) -> str:
    """Middle-clip arbitrary text to an exact character ceiling."""
    if limit <= 0:
        return ""
    marker = "\n… [clipped] …\n"
    if limit <= len(marker):
        return text[:limit]
    payload = limit - len(marker)
    head = (payload + 1) // 2
    tail = payload - head
    return text[:head] + marker + (text[-tail:] if tail else "")


def _one_line(text: str, limit: int = 80) -> str:
    return " ".join(text.split())[:limit]


def _right_clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    if limit == 1:
        return "…"
    return text[: limit - 1] + "…"


def _env_budget(name: str) -> int | None:
    raw = os.environ.get(f"MJJ_{name.upper()}_BUDGET")
    return int(raw) if raw and raw.isdigit() else None


@dataclass
class Budget:
    """Per-tool output budgets, in tokens.

    ``None`` means "whatever ``default`` is", so lowering ``default`` lowers
    everything — that is what an operator means by a tighter budget.
    """

    default: int = int(os.environ.get("MJJ_TOOL_BUDGET", "1600"))
    shell: int | None = None
    read: int | None = None
    search: int | None = None
    py: int | None = None
    skill: int | None = None

    def for_tool(self, name: str) -> int:
        explicit = getattr(self, name, None) if name != "default" else None
        if explicit is None:
            explicit = _env_budget(name)
        return int(explicit if explicit is not None else self.default)


@dataclass
class Drop:
    tool: str
    dropped_lines: int
    dropped_chars: int
    hint: str


@dataclass
class Ledger:
    """What the run has spent, and what it chose not to show the model."""

    budget: Budget = field(default_factory=Budget)
    tool_tokens: int = 0
    tool_calls: int = 0
    drops: list[Drop] = field(default_factory=list)

    def clip(self, tool: str, text: str, hint: str = "") -> str:
        """Fit ``text`` into ``tool``'s budget, keeping head and tail.

        Head and tail, not head alone: a failing command puts its diagnosis at
        the end (traceback, exit status) and its context at the start. The
        middle is the part nobody reads.
        """
        self.tool_calls += 1
        limit = max(0, self.budget.for_tool(tool) * CHARS_PER_TOKEN)
        if len(text) <= limit:
            self.tool_tokens += estimate_tokens(text)
            return text
        lines = text.splitlines()
        # Reserve enough room to keep the retrieval hint useful. The rest is
        # split head/tail because diagnostics usually end at the tail.
        marker_room = min(limit, max(24, min(160, limit * 2 // 3)))
        content_room = max(0, limit - marker_room - 2)
        head_chars = int(content_room * 0.55)
        tail_chars = content_room - head_chars
        head, size = [], 0
        for line in lines:
            if size + len(line) + 1 > head_chars:
                break
            head.append(line)
            size += len(line) + 1
        tail, size = [], 0
        for line in reversed(lines):
            if size + len(line) + 1 > tail_chars:
                break
            tail.append(line)
            size += len(line) + 1
        tail.reverse()
        dropped = len(lines) - len(head) - len(tail)
        if dropped <= 0 or not (head or tail):
            # Nothing line-shaped to keep: one enormous line, minified JSON, a
            # base64 blob. Clip through the middle instead.
            clipped = _clip_blob(text, limit)
            self.drops.append(Drop(tool, 0, len(text) - len(clipped), hint))
            self.tool_tokens += estimate_tokens(clipped)
            return clipped
        marker = f"… {dropped} line{'' if dropped == 1 else 's'} omitted"
        if hint:
            marker += f" — {_one_line(hint)}"
        marker += " …"
        marker = _right_clip(marker, marker_room)
        out = "\n".join([*head, marker, *tail])
        self.drops.append(
            Drop(tool, dropped, len(text) - len(out), hint)
        )
        self.tool_tokens += estimate_tokens(out)
        return out

    def summary(self) -> str:
        if not self.tool_calls:
            return "no tool output"
        dropped = sum(d.dropped_chars for d in self.drops)
        withheld_tokens = (dropped + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
        withheld = f" · ~{withheld_tokens} tokens withheld" if dropped else ""
        return f"{self.tool_calls} tool results · ~{self.tool_tokens} tokens{withheld}"
