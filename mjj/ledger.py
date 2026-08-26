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
from bisect import bisect_left, bisect_right
from collections import Counter
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


def _marker_room(limit: int) -> int:
    """Room for one omission marker; wide enough to keep a spill address."""
    return min(limit, max(24, min(160, limit * 2 // 3)))


_SIGNAL = re.compile(
    r"error|warning|\bfail|traceback|exception|assert|panic|fatal|denied"
    r"|refused|exit code|not found|undefined",
    re.IGNORECASE,
)
_ADDRESS_LINE = re.compile(r"^\s*\S+\.\w{1,8}:\d+")


def _is_signal(line: str) -> bool:
    """A line a reader would miss if it only saw the head and the tail."""
    return bool(_SIGNAL.search(line) or _ADDRESS_LINE.match(line))


def _dedupe(lines: list[str]) -> tuple[list[str], int]:
    """Collapse repeats of high-frequency lines; keep first occurrences."""
    counts = Counter(lines)
    seen: set[str] = set()
    kept: list[str] = []
    duplicates = 0
    for line in lines:
        if counts[line] >= 3 and line in seen:
            duplicates += 1
            continue
        seen.add(line)
        kept.append(line)
    return kept, duplicates


def _aggregate(text: str, limit: int, hint: str) -> tuple[str, int]:
    """Signal-aware replacement for positional head/tail clipping.

    Repeated lines collapse to their first occurrence. Diagnostic lines from
    anywhere in the output survive even when positional clipping would drop
    them. Whatever budget remains goes to the head (context) and tail (where
    exit status lives). The result never exceeds ``limit`` characters.
    """
    lines = text.splitlines()
    deduped, duplicates = _dedupe(lines)
    n = len(deduped)
    signals = [i for i, line in enumerate(deduped) if _is_signal(line)]

    def assemble(head_n: int, tail_n: int, kept: list[int]) -> str:
        middle = [i for i in kept if head_n <= i < n - tail_n]
        seq = (
            list(range(head_n))
            + middle
            + list(range(n - tail_n, n))
            if head_n + tail_n < n
            else list(range(n))
        )
        segments: list[tuple[int, int] | str] = []
        previous = -1
        for index in seq:
            if index > previous + 1:
                segments.append((previous + 1, index))
            segments.append(deduped[index])
            previous = index
        if previous < n - 1:
            segments.append((previous + 1, n))
        gaps = [seg for seg in segments if not isinstance(seg, str)]
        widest = max((end - start for start, end in gaps), default=0)
        out: list[str] = []
        rendered_lines = 0
        marker_room = max(24, min(_marker_room(limit), limit // 4))
        # A marker carrying the retrieval hint outranks decoration: it may
        # grow past the base share so the spill address survives clipping.
        hinted_room = min(limit - 1, _marker_room(limit) + min(120, len(hint)))
        if duplicates and not gaps:
            marker = (
                f"… {duplicates} duplicate line"
                f"{'' if duplicates == 1 else 's'} omitted"
            )
            carries_hint = bool(hint)
            if carries_hint:
                marker += f" — {_one_line(hint, 120)}"
            marker += " …"
            out.append(
                _right_clip(marker, hinted_room if carries_hint else marker_room)
            )
        for segment in segments:
            if isinstance(segment, str):
                out.append(segment)
                rendered_lines += 1
                continue
            start, end = segment
            omitted = end - start
            marker = f"… {omitted} line{'' if omitted == 1 else 's'} omitted"
            carries_hint = bool(hint) and omitted == widest
            if carries_hint:
                marker += f" — {_one_line(hint, 120)}"
            marker += " …"
            out.append(
                _right_clip(marker, hinted_room if carries_hint else marker_room)
            )
        return "\n".join(out), rendered_lines

    def fits(head_n: int, tail_n: int, kept: list[int]) -> bool:
        candidate, _ = assemble(head_n, tail_n, kept)
        return len(candidate) <= limit

    marker_slack = (
        2 * max(24, min(_marker_room(limit), limit // 4)) + min(120, len(hint)) + 8
    )

    # Signal budget first: floods of address-shaped lines must not starve the
    # few diagnostics that matter, so candidates compete on density (shortest
    # wins) under a hard cap. Everything left goes to head/tail context.
    signal_cap = 96
    signal_budget = max(0, limit - marker_slack)
    kept_signals: list[int] = []
    used = 0
    for index in sorted(signals, key=lambda i: (len(deduped[i]), i))[:signal_cap]:
        cost = len(deduped[index]) + 1
        if used + cost > signal_budget:
            break
        kept_signals.append(index)
        used += cost
    kept_signals.sort()

    prefix = [0]
    for line in deduped:
        prefix.append(prefix[-1] + len(line) + 1)

    def lines_within(room: int) -> int:
        return max(0, bisect_right(prefix, room) - 1)

    def lines_within_from_end(room: int) -> int:
        start = bisect_left(prefix, prefix[n] - room)
        return n - start

    positional_room = max(0, limit - used - marker_slack)
    head_n = min(n, lines_within(positional_room * 55 // 100))
    tail_n = min(
        max(0, n - head_n),
        lines_within_from_end(positional_room * 45 // 100),
    )
    while not fits(head_n, tail_n, kept_signals):
        if tail_n > 0:
            tail_n -= 1
        elif head_n > 0:
            head_n -= 1
        elif kept_signals:
            # Sacrifice density, not depth: drop the longest kept signal.
            longest = max(
                range(len(kept_signals)),
                key=lambda k: len(deduped[kept_signals[k]]),
            )
            kept_signals.pop(longest)
        else:
            break
    out, rendered = assemble(head_n, tail_n, kept_signals)
    if len(out) > limit:  # pathological tiny budgets still obey the bound
        out = _clip_blob(out, limit, "\n… [clipped] …\n")
        rendered = sum(
            1
            for line in out.splitlines()
            if not line.startswith("…")
        )
    return out, rendered


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
        """Fit ``text`` into ``tool``'s budget without losing the diagnosis.

        Overflowing output is aggregated: repeated lines collapse, signal
        lines (errors, warnings, ``file:line`` addresses) survive from
        anywhere, and the remaining budget goes to head/tail context. The
        full text is spilled to disk first so the model can always retrieve
        what was withheld.
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
        # Semantic compression is opt-in (MJJ_SUMMARIZER_URL) and never
        # load-bearing: any failure falls through to deterministic aggregation.
        summarized = self._semantic(text, limit, spill)
        if summarized is not None:
            self.drops.append(
                Drop(tool, text.count("\n") + 1 - summarized.count("\n") - 1,
                     len(text) - len(summarized), effective_hint)
            )
            self.tool_tokens += estimate_tokens(summarized)
            return summarized
        out, rendered = _aggregate(text, limit, effective_hint)
        if rendered == 0:
            # No source line was affordable: one enormous line, minified
            # JSON, a base64 blob. Clip through the middle instead.
            blob_marker = "\n… [clipped] …\n"
            if effective_hint:
                blob_marker = (
                    f"\n… [clipped — {_one_line(effective_hint, 120)}] …\n"
                )
            out = _clip_blob(text, limit, blob_marker)
            self.drops.append(
                Drop(tool, 0, len(text) - len(out), effective_hint)
            )
            self.tool_tokens += estimate_tokens(out)
            return out
        dropped = text.count("\n") + 1 - rendered
        self.drops.append(
            Drop(tool, max(0, dropped), len(text) - len(out), effective_hint)
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

    def _semantic(self, text: str, limit: int, spill: str) -> str | None:
        """Opt-in small-model compression; never load-bearing."""
        from .summarize import semantic_summary

        marker_room = min(limit, max(24, min(160, limit * 2 // 3)))
        content_room = max(64, limit - marker_room - 2)
        return semantic_summary(text, content_room, spill=spill)
