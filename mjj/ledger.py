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


@dataclass
class Budget:
    """Per-tool output budgets, in tokens."""

    default: int = int(os.environ.get("MJJ_TOOL_BUDGET", "1600"))
    shell: int = int(os.environ.get("MJJ_SHELL_BUDGET", "1600"))
    read: int = int(os.environ.get("MJJ_READ_BUDGET", "2400"))
    search: int = int(os.environ.get("MJJ_SEARCH_BUDGET", "1200"))
    py: int = int(os.environ.get("MJJ_PY_BUDGET", "1200"))

    def for_tool(self, name: str) -> int:
        return int(getattr(self, name, self.default))


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
        limit = self.budget.for_tool(tool) * CHARS_PER_TOKEN
        if len(text) <= limit:
            self.tool_tokens += estimate_tokens(text)
            return text
        lines = text.splitlines()
        head_chars = int(limit * 0.55)
        tail_chars = limit - head_chars - 120
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
        if dropped <= 0:  # pathological single-line blob
            keep = limit // 2
            clipped = text[:keep] + "\n… [clipped] …\n" + text[-keep:]
            self.drops.append(Drop(tool, 0, len(text) - len(clipped), hint))
            self.tool_tokens += estimate_tokens(clipped)
            return clipped
        marker = f"… {dropped} lines omitted"
        if hint:
            marker += f" — {hint}"
        marker += " …"
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
        withheld = f" · ~{estimate_tokens('x' * dropped)} tokens withheld" if dropped else ""
        return f"{self.tool_calls} tool results · ~{self.tool_tokens} tokens{withheld}"
