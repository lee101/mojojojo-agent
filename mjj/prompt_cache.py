"""Adaptive prompt-cache policy with bounded, process-local state.

The timing decision mirrors OpenPaths: frequent reuse gets a short cache,
moderately spaced reuse gets a longer cache, and sparse prefixes are not
written. Providers still own the actual KV cache; this module only emits safe
request hints and records returned usage.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field


CACHE_MODES = ("auto", "off", "implicit", "explicit")
MIN_CACHEABLE_CHARS = 4096
WINDOW_SECONDS = 2 * 60 * 60
MAX_PREFIXES = 512


@dataclass(frozen=True)
class CachePlan:
    key: str
    mode: str = ""
    breakpoint: bool = False
    ttl: str = ""


@dataclass
class _Prefix:
    times: list[float] = field(default_factory=list)
    last: float = 0.0


class PromptCacheOptimizer:
    def __init__(self, *, mode: str = "auto") -> None:
        if mode not in CACHE_MODES:
            mode = "auto"
        self.mode = mode
        self._prefixes: dict[str, _Prefix] = {}
        self.read_tokens = 0
        self.write_tokens = 0

    def openai_plan(
        self,
        model: str,
        instructions: str,
        tools: list[dict],
        *,
        now: float | None = None,
        observe: bool = True,
    ) -> CachePlan:
        key, chars = _prefix(model, instructions, tools)
        if self.mode == "implicit":
            return CachePlan(key=key, mode="implicit")
        if self.mode == "off" or chars < MIN_CACHEABLE_CHARS:
            return CachePlan(key=key, mode="explicit")
        if self.mode == "explicit":
            return CachePlan(key=key, mode="explicit", breakpoint=True, ttl="30m")
        times = (
            self._observe_times(key, now)
            if observe
            else self._times(key)
        )
        cache = _openai_cache_pays(times)
        return CachePlan(
            key=key,
            mode="explicit",
            breakpoint=cache,
            ttl="30m" if cache else "",
        )

    def anthropic_plan(
        self,
        model: str,
        instructions: str,
        tools: list[dict],
        *,
        now: float | None = None,
        observe: bool = True,
    ) -> CachePlan:
        key, chars = _prefix(model, instructions, tools)
        if self.mode in {"off", "implicit"} or chars < MIN_CACHEABLE_CHARS:
            return CachePlan(key=key)
        ttl = (
            "5m"
            if self.mode == "explicit"
            else (
                self._observe(key, now, cold="5m")
                if observe
                else self._decision(key, cold="5m")
            )
        )
        return CachePlan(key=key, breakpoint=ttl != "none", ttl=ttl)

    def record(self, *, read_tokens: int = 0, write_tokens: int = 0) -> None:
        self.read_tokens += max(0, int(read_tokens))
        self.write_tokens += max(0, int(write_tokens))

    def status(self) -> dict:
        return {
            "mode": self.mode,
            "prefixes": len(self._prefixes),
            "cache_read_tokens": self.read_tokens,
            "cache_write_tokens": self.write_tokens,
        }

    def _observe(
        self,
        key: str,
        now: float | None,
        *,
        cold: str = "none",
    ) -> str:
        return _decide_ttl(self._observe_times(key, now), cold=cold)

    def _observe_times(
        self,
        key: str,
        now: float | None,
    ) -> list[float]:
        current = time.time() if now is None else float(now)
        prefix = self._prefixes.get(key)
        if prefix is None:
            if len(self._prefixes) >= MAX_PREFIXES:
                oldest = min(
                    self._prefixes,
                    key=lambda item: self._prefixes[item].last,
                )
                del self._prefixes[oldest]
            prefix = self._prefixes[key] = _Prefix()
        cutoff = current - WINDOW_SECONDS
        prefix.times = [seen for seen in prefix.times if seen >= cutoff]
        prefix.times.append(current)
        prefix.last = current
        return prefix.times

    def _times(self, key: str) -> list[float]:
        prefix = self._prefixes.get(key)
        return prefix.times if prefix is not None else []

    def _decision(self, key: str, *, cold: str) -> str:
        prefix = self._prefixes.get(key)
        return _decide_ttl(prefix.times, cold=cold) if prefix is not None else cold


def _prefix(model: str, instructions: str, tools: list[dict]) -> tuple[str, int]:
    tool_text = json.dumps(tools, sort_keys=True, separators=(",", ":"))
    stable = f"{model}\0{instructions}\0{tool_text}"
    digest = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]
    return f"mjj:{digest}", len(instructions) + len(tool_text)


def _decide_ttl(times: list[float], *, cold: str = "none") -> str:
    if len(times) < 2:
        return cold
    gaps = [later - earlier for earlier, later in zip(times, times[1:])]
    writes_5m = 1 + sum(gap > 5 * 60 for gap in gaps)
    writes_1h = 1 + sum(gap > 60 * 60 for gap in gaps)
    requests = len(times)
    # Anthropic's common multipliers: 5m write 1.25x, 1h write 2x, read 0.1x.
    costs = {
        "none": float(requests),
        "5m": writes_5m * 1.25 + (requests - writes_5m) * 0.1,
        "1h": writes_1h * 2.0 + (requests - writes_1h) * 0.1,
    }
    return min(costs, key=costs.get)


def _openai_cache_pays(times: list[float]) -> bool:
    if len(times) < 2:
        return False
    writes = 1 + sum(
        later - earlier > 30 * 60
        for earlier, later in zip(times, times[1:])
    )
    reads = len(times) - writes
    cache_cost = writes * 1.25 + reads * 0.1
    return cache_cost < len(times)
