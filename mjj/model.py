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
# The model is allowed to think for a long time on hard turns; the read timeout
# has to outlast that or we kill our own reasoning.
READ_TIMEOUT = float(os.environ.get("MJJ_READ_TIMEOUT", "900"))


class ModelError(RuntimeError):
    def __init__(self, message: str, status: int = 0, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    requests: int = 0

    def add(self, doc: dict) -> None:
        self.requests += 1
        self.input_tokens += int(doc.get("input_tokens") or 0)
        self.output_tokens += int(doc.get("output_tokens") or 0)
        details = doc.get("input_tokens_details") or {}
        self.cached_input_tokens += int(details.get("cached_tokens") or 0)
        out_details = doc.get("output_tokens_details") or {}
        self.reasoning_tokens += int(out_details.get("reasoning_tokens") or 0)

    @property
    def billable_input(self) -> int:
        return max(0, self.input_tokens - self.cached_input_tokens)

    def summary(self) -> str:
        cache = (
            f" ({100 * self.cached_input_tokens / self.input_tokens:.0f}% cached)"
            if self.input_tokens
            else ""
        )
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
    resolver: CredentialResolver = field(default_factory=CredentialResolver)
    usage: Usage = field(default_factory=Usage)
    max_retries: int = 4

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
        if credential.kind == "chatgpt":
            # Without this the backend drops the reasoning items between turns,
            # and the model re-derives everything it already worked out — the
            # single most expensive mistake a harness can make here.
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
        while True:
            credential = self.resolver.resolve(force=attempt > 0)
            body = self.request_body(input_items, instructions, tools, credential)
            try:
                yield from self._stream_once(credential, body)
                return
            except ModelError as exc:
                attempt += 1
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
                yield event


def _retryable_message(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in ("rate limit", "overloaded", "timeout", "temporarily")
    )


def _decode_sse(stream) -> Iterator[Event]:
    """Minimal SSE decode. We only need ``data:`` lines; the ``event:`` line
    duplicates the ``type`` field inside the payload."""
    for raw in stream:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            doc = json.loads(payload)
        except ValueError:
            continue
        yield Event(type=doc.get("type", ""), data=doc)


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
