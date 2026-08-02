"""Streaming Responses API client.

Two backends, one shape:

* ``chatgpt``  — the max-plan backend. Streaming is mandatory, ``store`` must
  be false, and reasoning items must be echoed back verbatim on the next turn
  or the model loses its train of thought.
* ``api_key`` — the public API, same wire format.

Stdlib only. An agent harness that needs a 40 MB dependency tree to send one
POST is not a token-efficient harness, it is a slow one.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterator

from .auth import AuthError, Credential, CredentialResolver

DEFAULT_MODEL = os.environ.get("MJJ_MODEL", "gpt-5.6-sol")
DEFAULT_EFFORT = os.environ.get("MJJ_EFFORT", "high")
DEFAULT_VERBOSITY = os.environ.get("MJJ_VERBOSITY", "low")
# The model is allowed to think for a long time on hard turns; the read timeout
# has to outlast that or we kill our own reasoning.
READ_TIMEOUT = float(os.environ.get("MJJ_READ_TIMEOUT", "900"))
COMPACT_THRESHOLD = int(os.environ.get("MJJ_COMPACT_THRESHOLD", "200000"))


class ModelError(RuntimeError):
    def __init__(self, message: str, status: int = 0, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    requests: int = 0

    def add(self, doc: dict) -> None:
        self.requests += 1
        self.input_tokens += int(doc.get("input_tokens") or 0)
        self.output_tokens += int(doc.get("output_tokens") or 0)
        details = doc.get("input_tokens_details") or {}
        self.cached_input_tokens += int(details.get("cached_tokens") or 0)
        self.cache_write_tokens += int(details.get("cache_write_tokens") or 0)
        out_details = doc.get("output_tokens_details") or {}
        self.reasoning_tokens += int(out_details.get("reasoning_tokens") or 0)

    @property
    def billable_input(self) -> int:
        return max(0, self.input_tokens - self.cached_input_tokens)

    def summary(self) -> str:
        cache_parts = []
        if self.input_tokens and self.cached_input_tokens:
            cache_parts.append(
                f"{100 * self.cached_input_tokens / self.input_tokens:.0f}% cached"
            )
        if self.cache_write_tokens:
            cache_parts.append(f"{self.cache_write_tokens} cache write")
        cache = f" ({', '.join(cache_parts)})" if cache_parts else ""
        return (
            f"{self.requests} req · in {self.input_tokens}{cache} · "
            f"out {self.output_tokens} (reasoning {self.reasoning_tokens})"
        )


@dataclass
class Event:
    """One decoded SSE event. ``type`` is the Responses event name."""

    type: str
    data: dict = field(default_factory=dict)

    @property
    def delta(self) -> str:
        return self.data.get("delta") or ""

    @property
    def item(self) -> dict:
        return self.data.get("item") or {}


@dataclass
class ModelClient:
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    summary: str = "auto"
    verbosity: str = DEFAULT_VERBOSITY
    resolver: CredentialResolver = field(default_factory=CredentialResolver)
    usage: Usage = field(default_factory=Usage)
    max_retries: int = 4
    compact_threshold: int = COMPACT_THRESHOLD
    # Routes every request in one session to the same cache shard. Without it
    # a long session keeps missing a cache it just populated, and the input
    # side of the bill is the largest number in this file.
    cache_key: str = ""
    _compaction_disabled: bool = field(default=False, init=False, repr=False)

    def request_body(
        self,
        input_items: list[dict],
        instructions: str,
        tools: list[dict],
        credential: Credential,
    ) -> dict:
        body: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": input_items,
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "stream": True,
            "store": False,
            "reasoning": {"effort": self.effort, "summary": self.summary},
        }
        if self.verbosity:
            body["text"] = {"verbosity": self.verbosity}
        if self.cache_key:
            body["prompt_cache_key"] = self.cache_key
        if self.compact_threshold > 0 and not self._compaction_disabled:
            body["context_management"] = [
                {
                    "type": "compaction",
                    "compact_threshold": self.compact_threshold,
                }
            ]
        # History is managed locally with store=false, so encrypted reasoning
        # must be replayable on both the public and ChatGPT Responses backends.
        body["include"] = ["reasoning.encrypted_content"]
        return body

    def stream(
        self,
        input_items: list[dict],
        instructions: str,
        tools: list[dict] | None = None,
    ) -> Iterator[Event]:
        tools = tools or []
        attempt = 0
        auth_failures = 0
        while True:
            credential = self.resolver.resolve(
                force=auth_failures == 1,
                fallback=auth_failures > 1,
            )
            body = self.request_body(input_items, instructions, tools, credential)
            try:
                yield from self._stream_once(credential, body)
                return
            except ModelError as exc:
                attempt += 1
                if _compaction_unsupported(exc, body):
                    # Older models and the ChatGPT backend may lag the public
                    # Responses API. Compaction is an optimisation, not a
                    # reason to lose the run.
                    self._compaction_disabled = True
                    continue
                if exc.status == 401:
                    auth_failures += 1
                if not exc.retryable or attempt > self.max_retries:
                    raise
                # 0.5s, 1s, 2s, 4s. Server-side rate limits on the max plan
                # clear on the order of seconds, not minutes.
                time.sleep(0.5 * (2 ** (attempt - 1)))

    def _stream_once(self, credential: Credential, body: dict) -> Iterator[Event]:
        url = credential.base_url.rstrip("/") + "/responses"
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), method="POST"
        )
        for key, value in credential.headers.items():
            req.add_header(key, value)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "text/event-stream")
        try:
            resp = urllib.request.urlopen(req, timeout=READ_TIMEOUT)
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", "replace").strip()
            raise ModelError(
                f"HTTP {exc.code}: {detail[:600]}",
                status=exc.code,
                retryable=exc.code in (401, 408, 409, 429, 500, 502, 503, 504),
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ModelError(f"connection failed: {exc}", retryable=True) from exc
        with resp:
            for event in _decode_sse(resp):
                if event.type == "response.completed":
                    usage = (event.data.get("response") or {}).get("usage") or {}
                    self.usage.add(usage)
                if event.type in ("response.failed", "error"):
                    err = event.data.get("error") or event.data
                    message = err.get("message") if isinstance(err, dict) else str(err)
                    raise ModelError(
                        f"stream failed: {message}",
                        retryable=_retryable_message(str(message)),
                    )
                if event.type == "response.incomplete":
                    response = event.data.get("response") or {}
                    details = response.get("incomplete_details") or {}
                    reason = details.get("reason") or response.get("status") or "unknown"
                    raise ModelError(f"response incomplete: {reason}")
                yield event


def _retryable_message(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in ("rate limit", "overloaded", "timeout", "temporarily")
    )


def _compaction_unsupported(exc: ModelError, body: dict) -> bool:
    if exc.status != 400 or "context_management" not in body:
        return False
    detail = str(exc).lower()
    return "context_management" in detail or "compact" in detail


def _decode_sse(stream) -> Iterator[Event]:
    """Decode SSE records, including payloads split across ``data:`` lines."""
    data_lines: list[str] = []
    for raw in stream:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            event = _event_from_data(data_lines)
            data_lines = []
            if event is not None:
                yield event
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    event = _event_from_data(data_lines)
    if event is not None:
        yield event


def _event_from_data(lines: list[str]) -> Event | None:
    payload = "\n".join(lines).strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        doc = json.loads(payload)
    except ValueError:
        return None
    return Event(type=doc.get("type", ""), data=doc)


def probe(model: str = DEFAULT_MODEL) -> dict:
    """One tiny round trip. Used by ``mjj auth status --probe`` and by the
    smoke test, so a broken credential fails loudly instead of at turn 40."""
    client = ModelClient(model=model, effort="low", summary="auto")
    text = []
    try:
        for event in client.stream(
            [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "reply with: ok"}],
                }
            ],
            instructions="Reply with exactly one word.",
        ):
            if event.type == "response.output_text.delta":
                text.append(event.delta)
    except (ModelError, AuthError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "model": model, "text": "".join(text).strip()[:40],
            "usage": client.usage.summary()}
