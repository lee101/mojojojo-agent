"""Token accounting and the one truncation policy every tool obeys.

The rule that makes this harness cheap: a tool result is a *summary with an
address*, never a dump. If output does not fit its budget, the middle goes and
the tool says exactly what it dropped and how to get it back. The model can
always ask for the rest; it can never be forced to pay for the rest.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

# ~4 chars per token is close enough for budgeting and costs nothing. Swapping
# in a real BPE counter changes the numbers by a few percent and costs a
# dependency plus milliseconds per call.
CHARS_PER_TOKEN = 4
SPILL_RETENTION_SECONDS = 7 * 24 * 60 * 60
SPILL_MAX_FILES = 256


def estimate_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def _clip_blob(
    text: str,
    limit: int,
    marker: str = "\n… [clipped] …\n",
) -> str:
    """Middle-clip arbitrary text to an exact character ceiling."""
    if limit <= 0:
        return ""
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
    spill_root: Path | None = field(default=None, repr=False)
    spill_prefix: str = field(default=".mjj/tool-results", repr=False)
    _spill_sequence: int = field(default=0, init=False, repr=False)

    def bind_workspace(self, cwd: str | Path) -> None:
        """Enable recoverable full outputs for future clipped tool results."""
        root = Path(cwd).expanduser().resolve()
        destination = root / ".mjj" / "tool-results"
        try:
            destination.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(destination, 0o700)
        except OSError:
            return
        self.spill_root = destination
        self.spill_prefix = ".mjj/tool-results"
        self._cleanup_spills()

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
        spill = self._spill(tool, text)
        effective_hint = hint
        if spill:
            effective_hint = f"full output: {spill}"
            if hint:
                effective_hint += f"; {hint}"
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
            blob_marker = "\n… [clipped] …\n"
            if effective_hint:
                blob_marker = (
                    f"\n… [clipped — {_one_line(effective_hint, 120)}] …\n"
                )
            clipped = _clip_blob(text, limit, blob_marker)
            self.drops.append(
                Drop(tool, 0, len(text) - len(clipped), effective_hint)
            )
            self.tool_tokens += estimate_tokens(clipped)
            return clipped
        marker = f"… {dropped} line{'' if dropped == 1 else 's'} omitted"
        if effective_hint:
            marker += f" — {_one_line(effective_hint, 120)}"
        marker += " …"
        marker = _right_clip(marker, marker_room)
        out = "\n".join([*head, marker, *tail])
        self.drops.append(
            Drop(tool, dropped, len(text) - len(out), effective_hint)
        )
        self.tool_tokens += estimate_tokens(out)
        return out

    def attach(self, tool: str, current: str, suffix: str) -> str:
        """Attach one-time context without recording a second tool call."""
        if not suffix:
            return current
        limit = max(0, self.budget.for_tool(tool) * CHARS_PER_TOKEN)
        if limit == 0:
            self.tool_tokens -= estimate_tokens(current)
            self.drops.append(
                Drop(
                    tool,
                    0,
                    len(current) + len(suffix),
                    "scoped project instructions attached",
                )
            )
            return ""
        separator = "\n\n"
        combined = current + separator + suffix
        if len(combined) <= limit:
            self.tool_tokens += estimate_tokens(combined) - estimate_tokens(current)
            return combined
        suffix_room = min(len(suffix), max(1, limit * 2 // 3))
        kept_suffix = _clip_blob(suffix, suffix_room)
        current_room = max(0, limit - len(kept_suffix) - len(separator))
        kept_current = _clip_blob(current, current_room)
        joiner = separator if kept_current and kept_suffix else ""
        result = kept_current + joiner + kept_suffix
        # Defend the accounting invariant even at adversarial one-character
        # budgets where no useful marker can fit.
        result = result[:limit]
        self.tool_tokens += estimate_tokens(result) - estimate_tokens(current)
        self.drops.append(
            Drop(
                tool,
                0,
                len(combined) - len(result),
                "scoped project instructions attached",
            )
        )
        return result

    def _spill(self, tool: str, text: str) -> str:
        if self.spill_root is None:
            return ""
        self._spill_sequence += 1
        safe_tool = (
            re.sub(r"[^a-z0-9_-]+", "-", tool.lower()).strip("-") or "tool"
        )
        filename = f"{time.time_ns()}-{safe_tool}-{self._spill_sequence}.txt"
        destination = self.spill_root / filename
        try:
            with destination.open("x", encoding="utf-8") as output:
                output.write(text)
            os.chmod(destination, 0o600)
        except OSError:
            return ""
        return f"{self.spill_prefix}/{filename}"

    def _cleanup_spills(self) -> None:
        if self.spill_root is None:
            return
        try:
            entries = sorted(
                (path for path in self.spill_root.iterdir() if path.is_file()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        cutoff = time.time() - SPILL_RETENTION_SECONDS
        for position, path in enumerate(entries):
            try:
                if position >= SPILL_MAX_FILES or path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def summary(self) -> str:
        if not self.tool_calls:
            return "no tool output"
        dropped = sum(d.dropped_chars for d in self.drops)
        withheld_tokens = (dropped + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
        withheld = f" · ~{withheld_tokens} tokens withheld" if dropped else ""
        return f"{self.tool_calls} tool results · ~{self.tool_tokens} tokens{withheld}"
