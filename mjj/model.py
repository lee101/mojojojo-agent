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
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterator

from .auth import AuthError, Credential, CredentialResolver
from .model_routes import closest_effort, resolve_model, supported_efforts
from .prompt import for_model as prompt_for_model
from .prompt import split_cache_layers
from .prompt_cache import CACHE_MODES, PromptCacheOptimizer

DEFAULT_MODEL = os.environ.get("MJJ_MODEL", "gpt-5.6-sol")
DEFAULT_EFFORT = os.environ.get("MJJ_EFFORT", "high")
DEFAULT_VERBOSITY = os.environ.get("MJJ_VERBOSITY", "low")
# The model is allowed to think for a long time on hard turns; the read timeout
# has to outlast that or we kill our own reasoning.
READ_TIMEOUT = float(os.environ.get("MJJ_READ_TIMEOUT", "900"))
COMPACT_THRESHOLD = int(os.environ.get("MJJ_COMPACT_THRESHOLD", "200000"))
DEFAULT_CACHE_MODE = os.environ.get("MJJ_CACHE_MODE", "auto").strip().lower()
if DEFAULT_CACHE_MODE not in CACHE_MODES:
    DEFAULT_CACHE_MODE = "auto"


class ModelError(RuntimeError):
    def __init__(self, message: str, status: int = 0, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def _http_error_message(status: int, detail: str) -> str:
    """Turn provider HTTP failures into actionable agent errors."""
    text = (detail or "").strip()
    lower = text.lower()
    billing = status == 402 or any(
        needle in lower
        for needle in (
            "insufficient balance",
            "insufficient credits",
            "out of credits",
            "payment required",
            "quota exceeded",
            "billing",
        )
    )
    if billing:
        hint = (
            "Provider reports insufficient balance/credits. "
            "Top up the provider account (DeepSeek: https://platform.deepseek.com), "
            "or switch with `mjj /provider openpaths` / `--provider openai`."
        )
        body = text[:400] if text else "payment required"
        return f"HTTP {status}: {body} — {hint}"
    clipped = text[:600] if text else "no response body"
    return f"HTTP {status}: {clipped}"


def _raise_http_error(exc: urllib.error.HTTPError) -> None:
    detail = exc.read(4096).decode("utf-8", "replace").strip()
    raise ModelError(
        _http_error_message(exc.code, detail),
        status=exc.code,
        retryable=exc.code in (401, 408, 409, 429, 500, 502, 503, 504),
    ) from exc


def _body_effort(body: dict) -> str:
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        return str(reasoning.get("effort") or "")
    return str(body.get("reasoning_effort") or "")


def _supported_efforts_from_error(
    exc: ModelError, body: dict
) -> tuple[str, ...] | None:
    """Learn model capabilities from a provider's structured 400 message."""
    if exc.status != 400 or not _body_effort(body):
        return None
    message = str(exc)
    if "unsupported" not in message.lower() or "effort" not in message.lower():
        return None
    tail = message.lower().split("supported values", 1)
    if len(tail) != 2:
        return None
    values = re.findall(r"['\"](none|minimal|low|medium|high|xhigh|max)['\"]", tail[1])
    return tuple(dict.fromkeys(values)) or None


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

    def merge(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.cached_input_tokens += other.cached_input_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.output_tokens += other.output_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.requests += other.requests

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
    model: str = "auto"
    provider: str = "auto"
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
    max_output_tokens: int = 0
    cache_mode: str = DEFAULT_CACHE_MODE
    cache_optimizer: PromptCacheOptimizer = field(
        default_factory=PromptCacheOptimizer
    )
    _compaction_disabled: bool = field(default=False, init=False, repr=False)
    _cache_controls_disabled: bool = field(default=False, init=False, repr=False)
    _effort_overrides: dict[str, tuple[str, ...]] = field(
        default_factory=dict, init=False, repr=False
    )
    _reported_effort_adjustments: set[tuple[str, str, str]] = field(
        default_factory=set, init=False, repr=False
    )
    last_effective_effort: str = field(default="", init=False)

    def __post_init__(self) -> None:
        if self.cache_mode not in CACHE_MODES:
            self.cache_mode = "auto"
        self.cache_optimizer.mode = self.cache_mode
        self.resolver.model = self.model
        self.resolver.provider = self.provider

    def request_body(
        self,
        input_items: list[dict],
        instructions: str,
        tools: list[dict],
        credential: Credential,
        *,
        observe_cache: bool = True,
    ) -> dict:
        model = self.effective_model(credential)
        rendered_instructions = prompt_for_model(instructions, model)
        body: dict[str, Any] = {
            "model": model,
            "instructions": rendered_instructions,
            "input": input_items,
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "stream": True,
            "store": False,
            "reasoning": {
                "effort": self.effective_effort(model),
                "summary": self.summary,
            },
        }
        if self.verbosity:
            body["text"] = {"verbosity": self.verbosity}
        if (
            not self._cache_controls_disabled
            and _supports_explicit_openai_cache(model, credential)
        ):
            plan = self.cache_optimizer.openai_plan(
                model,
                rendered_instructions,
                tools,
                observe=observe_cache,
            )
            body["prompt_cache_options"] = {"mode": plan.mode}
            if plan.ttl:
                body["prompt_cache_options"]["ttl"] = plan.ttl
            # Prefix-derived keys share KV across sessions with the same spine.
            if self.cache_mode != "off":
                body["prompt_cache_key"] = plan.key
            if plan.breakpoint:
                body["input"] = [_openai_cache_boundary(), *input_items]
        elif self.cache_mode != "off" and self.cache_key:
            body["prompt_cache_key"] = self.cache_key
        if self.max_output_tokens > 0:
            body["max_output_tokens"] = self.max_output_tokens
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

    def effective_model(self, credential: Credential) -> str:
        return resolve_model(self.model, credential.provider, credential.default_model)

    def effective_effort(self, model: str = "") -> str:
        supported = self._effort_overrides.get(model) or (
            supported_efforts(model) if model else None
        )
        effective = closest_effort(self.effort, supported) if supported else self.effort
        self.last_effective_effort = effective
        return effective

    def cache_status(self) -> dict:
        status = self.cache_optimizer.status()
        status["mode"] = self.cache_mode
        return status

    def set_cache_mode(self, mode: str) -> None:
        normalized = mode.strip().lower()
        if normalized not in CACHE_MODES:
            raise ValueError(f"cache mode must be one of: {', '.join(CACHE_MODES)}")
        self.cache_mode = normalized
        self.cache_optimizer.mode = normalized

    def chat_request_body(
        self,
        input_items: list[dict],
        instructions: str,
        tools: list[dict],
        credential: Credential,
        *,
        observe_cache: bool = True,
    ) -> dict:
        model = self.effective_model(credential)
        rendered_instructions = prompt_for_model(instructions, model)
        body: dict[str, Any] = {
            "model": model,
            "messages": _chat_messages(
                input_items,
                rendered_instructions,
            ),
            "tools": [_chat_tool(tool) for tool in tools],
            "tool_choice": "auto" if tools else None,
            "stream": True,
        }
        if credential.provider == "openrouter":
            body["reasoning"] = {"effort": self.effective_effort(model)}
        elif credential.provider == "deepseek" or _is_deepseek_model(model):
            thinking, mapped = _deepseek_thinking(self.effort)
            body["thinking"] = thinking
            if mapped is not None:
                body["reasoning_effort"] = mapped
        else:
            body["reasoning_effort"] = self.effective_effort(model)
        if (
            not self._cache_controls_disabled
            and credential.provider in {"openpaths", "openrouter"}
            and _is_anthropic_model(model)
        ):
            plan = self.cache_optimizer.anthropic_plan(
                model,
                rendered_instructions,
                tools,
                observe=observe_cache,
            )
            if plan.breakpoint:
                stable, volatile = split_cache_layers(rendered_instructions)
                blocks = [
                    {
                        "type": "text",
                        "text": stable,
                        "cache_control": {"type": "ephemeral", "ttl": plan.ttl},
                    }
                ]
                if volatile:
                    blocks.append({"type": "text", "text": volatile})
                body["messages"][0]["content"] = blocks
        if self.max_output_tokens > 0:
            body["max_tokens"] = self.max_output_tokens
        return {key: value for key, value in body.items() if value is not None}

    def stream(
        self,
        input_items: list[dict],
        instructions: str,
        tools: list[dict] | None = None,
    ) -> Iterator[Event]:
        tools = tools or []
        attempt = 0
        auth_failures = 0
        emitted_content = False
        while True:
            self.resolver.model = self.model
            self.resolver.provider = self.provider
            credential = self.resolver.resolve(
                force=auth_failures == 1,
                fallback=auth_failures > 1,
            )
            body = (
                self.chat_request_body(
                    input_items,
                    instructions,
                    tools,
                    credential,
                    observe_cache=attempt == 0,
                )
                if credential.api_style == "chat_completions"
                else self.request_body(
                    input_items,
                    instructions,
                    tools,
                    credential,
                    observe_cache=attempt == 0,
                )
            )
            requested_effort = self.effort.strip().lower()
            sent_effort = _body_effort(body)
            adjustment = (str(body.get("model", "")), requested_effort, sent_effort)
            if (
                sent_effort != requested_effort
                and adjustment not in self._reported_effort_adjustments
            ):
                self._reported_effort_adjustments.add(adjustment)
                yield Event(
                    "mjj.effort_adjusted",
                    {
                        "model": adjustment[0],
                        "requested": requested_effort,
                        "effective": sent_effort,
                        "message": (
                            f"reasoning {requested_effort} → {sent_effort} "
                            f"for {adjustment[0]}"
                        ),
                    },
                )
            try:
                for event in self._stream_once(credential, body):
                    if event.type in {
                        "response.output_text.delta",
                        "response.reasoning_summary_text.delta",
                        "response.output_item.done",
                    }:
                        emitted_content = True
                    yield event
                return
            except ModelError as exc:
                supported = _supported_efforts_from_error(exc, body)
                if supported:
                    model = str(body.get("model", ""))
                    previous = _body_effort(body)
                    effective = closest_effort(previous, supported)
                    if effective != previous:
                        self._effort_overrides[model] = supported
                        continue
                attempt += 1
                if _compaction_unsupported(exc, body):
                    # Older models and the ChatGPT backend may lag the public
                    # Responses API. Compaction is an optimisation, not a
                    # reason to lose the run.
                    self._compaction_disabled = True
                    continue
                if _cache_controls_unsupported(exc, body):
                    self._cache_controls_disabled = True
                    continue
                if exc.status == 401:
                    auth_failures += 1
                # Once semantic output is visible, replaying the request can
                # duplicate text or execute a tool twice. Surface the broken
                # stream and let the next user turn recover explicitly.
                if emitted_content:
                    raise
                if not exc.retryable or attempt > self.max_retries:
                    if exc.retryable and not _stream_required(exc):
                        yield Event(
                            "mjj.request_fallback",
                            {"message": "stream unavailable; retrying as one request"},
                        )
                        yield from self._request_once(credential, body)
                        return
                    raise
                yield Event(
                    "mjj.retry",
                    {
                        "attempt": attempt,
                        "max_retries": self.max_retries,
                        "message": str(exc),
                        "delay": 0.5 * (2 ** (attempt - 1)),
                    },
                )
                # 0.5s, 1s, 2s, 4s. Server-side rate limits on the max plan
                # clear on the order of seconds, not minutes.
                time.sleep(0.5 * (2 ** (attempt - 1)))

    def _request_once(self, credential: Credential, body: dict) -> Iterator[Event]:
        """Non-streaming compatibility path used only before any delta arrived."""
        request_body = dict(body)
        request_body["stream"] = False
        endpoint = (
            "/chat/completions"
            if credential.api_style == "chat_completions"
            else "/responses"
        )
        req = urllib.request.Request(
            credential.base_url.rstrip("/") + endpoint,
            data=json.dumps(request_body).encode(),
            method="POST",
        )
        for key, value in credential.headers.items():
            req.add_header(key, value)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=READ_TIMEOUT) as resp:
                document = json.load(resp)
        except urllib.error.HTTPError as exc:
            _raise_http_error(exc)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise ModelError(f"request fallback failed: {exc}") from exc
        if credential.api_style == "chat_completions":
            yield from self._normalize_chat_response(document)
            return
        for item in document.get("output") or []:
            yield Event("response.output_item.done", {"item": item})
        usage = document.get("usage") or {}
        self.usage.add(usage)
        self._record_cache_usage(usage)
        yield Event("response.completed", {"response": {"usage": usage}})

    def _normalize_chat_response(self, document: dict) -> Iterator[Event]:
        choices = document.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
        if isinstance(reasoning, str) and reasoning:
            yield Event(
                "response.reasoning_summary_text.delta",
                {"delta": reasoning},
            )
        tool_calls = message.get("tool_calls") or []
        content = message.get("content") or ""
        if tool_calls:
            for index, call in enumerate(tool_calls):
                function = call.get("function") or {}
                item: dict[str, Any] = {
                    "type": "function_call",
                    "call_id": call.get("id") or f"call_{index}",
                    "name": function.get("name", ""),
                    "arguments": function.get("arguments", "{}"),
                }
                if index == 0:
                    if reasoning:
                        item["reasoning_content"] = reasoning
                    item["content"] = content if isinstance(content, str) else ""
                yield Event("response.output_item.done", {"item": item})
        elif content:
            item = {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": content}],
            }
            if isinstance(reasoning, str) and reasoning:
                item["reasoning_content"] = reasoning
            yield Event("response.output_text.delta", {"delta": content})
            yield Event("response.output_item.done", {"item": item})
        usage = _chat_usage(document.get("usage") or {})
        self.usage.add(usage)
        self._record_cache_usage(usage)
        yield Event("response.completed", {"response": {"usage": usage}})

    def _stream_once(self, credential: Credential, body: dict) -> Iterator[Event]:
        if credential.api_style == "chat_completions":
            yield from self._stream_chat_once(credential, body)
            return
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
            _raise_http_error(exc)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ModelError(f"connection failed: {exc}", retryable=True) from exc
        with resp:
            for event in _decode_sse(resp):
                if event.type in ("response.completed", "response.incomplete"):
                    usage = (event.data.get("response") or {}).get("usage") or {}
                    self.usage.add(usage)
                    self._record_cache_usage(usage)
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
                    reason = (
                        details.get("reason")
                        or response.get("status")
                        or "unknown"
                    )
                    raise ModelError(f"response incomplete: {reason}")
                yield event

    def _stream_chat_once(
        self, credential: Credential, body: dict
    ) -> Iterator[Event]:
        """Translate OpenAI-compatible chat streaming into Responses events.

        Keeping the translation here means the agent loop, transcript and all
        tools remain provider-independent. OpenPaths, OpenRouter and local
        compatible gateways therefore get the same coding-agent behaviour.
        """
        url = credential.base_url.rstrip("/") + "/chat/completions"
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
            _raise_http_error(exc)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ModelError(f"connection failed: {exc}", retryable=True) from exc

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict] = {}
        usage: dict = {}
        with resp:
            for chunk in _decode_sse_documents(resp):
                if chunk.get("error"):
                    error = chunk["error"]
                    message = error.get("message") if isinstance(error, dict) else error
                    raise ModelError(
                        f"stream failed: {message}",
                        retryable=_retryable_message(str(message)),
                    )
                usage = chunk.get("usage") or usage
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta")
                is_delta = isinstance(delta, dict)
                payload = delta if is_delta else (choice.get("message") or {})
                if not isinstance(payload, dict):
                    continue
                reasoning = payload.get("reasoning_content") or payload.get("reasoning")
                if isinstance(reasoning, str) and reasoning:
                    if is_delta or not reasoning_parts:
                        reasoning_parts.append(reasoning)
                        yield Event(
                            "response.reasoning_summary_text.delta",
                            {"delta": reasoning},
                        )
                content = payload.get("content")
                if isinstance(content, str) and content:
                    if is_delta or not text_parts:
                        text_parts.append(content)
                        yield Event("response.output_text.delta", {"delta": content})
                for piece in payload.get("tool_calls") or []:
                    index = int(piece.get("index") or 0)
                    call = calls.setdefault(
                        index,
                        {"id": "", "name": "", "arguments": []},
                    )
                    call["id"] = piece.get("id") or call["id"]
                    function = piece.get("function") or {}
                    call["name"] = function.get("name") or call["name"]
                    arguments = function.get("arguments")
                    if arguments:
                        if is_delta or not call["arguments"]:
                            call["arguments"].append(arguments)

        reasoning_text = "".join(reasoning_parts)
        content_text = "".join(text_parts)
        if calls:
            first_index = min(calls)
            for index in sorted(calls):
                call = calls[index]
                call_id = call["id"] or f"call_{index}"
                item: dict[str, Any] = {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": call["name"],
                    "arguments": "".join(call["arguments"]),
                }
                # DeepSeek requires reasoning_content on tool turns to be echoed.
                if index == first_index:
                    if reasoning_text:
                        item["reasoning_content"] = reasoning_text
                    item["content"] = content_text
                yield Event("response.output_item.done", {"item": item})
        elif content_text or reasoning_text:
            item = {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": content_text}],
            }
            if reasoning_text:
                item["reasoning_content"] = reasoning_text
            yield Event("response.output_item.done", {"item": item})
        normalized_usage = _chat_usage(usage)
        self.usage.add(normalized_usage)
        self._record_cache_usage(normalized_usage)
        yield Event("response.completed", {"response": {"usage": normalized_usage}})

    def _record_cache_usage(self, usage: dict) -> None:
        details = usage.get("input_tokens_details") or {}
        self.cache_optimizer.record(
            read_tokens=details.get("cached_tokens", 0),
            write_tokens=details.get("cache_write_tokens", 0),
        )


def _retryable_message(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in ("rate limit", "overloaded", "timeout", "temporarily")
    )


def _stream_required(exc: ModelError) -> bool:
    """Some gateways reject the non-stream fallback; do not mask the real error."""
    return "stream must be set to true" in str(exc).lower()


def _chat_usage(usage: dict) -> dict:
    """Normalize Chat Completions token names to Responses names."""
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    return {
        "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens", 0)),
        "output_tokens": usage.get(
            "output_tokens", usage.get("completion_tokens", 0)
        ),
        "input_tokens_details": {
            "cached_tokens": prompt_details.get(
                "cached_tokens",
                usage.get(
                    "cache_read_input_tokens",
                    usage.get("cache_read_tokens", 0),
                ),
            ),
            "cache_write_tokens": prompt_details.get(
                "cache_write_tokens",
                usage.get(
                    "cache_creation_input_tokens",
                    usage.get("cache_write_tokens", 0),
                ),
            ),
        },
        "output_tokens_details": {
            "reasoning_tokens": completion_details.get("reasoning_tokens", 0),
        },
    }


def _supports_explicit_openai_cache(model: str, credential: Credential) -> bool:
    normalized = model.lower().split("/")[-1]
    return (
        credential.provider == "openai"
        and credential.kind == "api_key"
        and normalized.startswith("gpt-5.6")
    )


def _is_anthropic_model(model: str) -> bool:
    lowered = model.lower()
    return "claude" in lowered or lowered.startswith("anthropic/")


def _openai_cache_boundary() -> dict:
    return {
        "type": "message",
        "role": "developer",
        "content": [
            {
                "type": "input_text",
                "text": "Stable MJJ instructions and tool contract boundary.",
                "prompt_cache_breakpoint": {"mode": "explicit"},
            }
        ],
    }


def _compaction_unsupported(exc: ModelError, body: dict) -> bool:
    if exc.status != 400 or "context_management" not in body:
        return False
    detail = str(exc).lower()
    return "context_management" in detail or "compact" in detail


def _cache_controls_unsupported(exc: ModelError, body: dict) -> bool:
    if exc.status != 400:
        return False
    encoded = json.dumps(body, separators=(",", ":"))
    if not any(
        field in encoded
        for field in ("prompt_cache_options", "prompt_cache_breakpoint", "cache_control")
    ):
        return False
    detail = str(exc).lower()
    return any(token in detail for token in ("cache", "breakpoint", "ttl"))


def _decode_sse(stream) -> Iterator[Event]:
    """Decode SSE records, including payloads split across ``data:`` lines."""
    for doc in _decode_sse_documents(stream):
        yield Event(type=doc.get("type", ""), data=doc)


def _decode_sse_documents(stream) -> Iterator[dict]:
    """Decode SSE payloads without assuming Responses or Chat shape."""
    data_lines: list[str] = []
    for raw in stream:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            event = _document_from_data(data_lines)
            data_lines = []
            if event is not None:
                yield event
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    event = _document_from_data(data_lines)
    if event is not None:
        yield event


def _event_from_data(lines: list[str]) -> Event | None:
    doc = _document_from_data(lines)
    return Event(type=doc.get("type", ""), data=doc) if doc is not None else None


def _document_from_data(lines: list[str]) -> dict | None:
    payload = "\n".join(lines).strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        doc = json.loads(payload)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def _chat_tool(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters") or {"type": "object"},
        },
    }


def _chat_messages(items: list[dict], instructions: str) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": instructions}]
    index = 0
    while index < len(items):
        item = items[index]
        item_type = item.get("type")
        if item_type == "message":
            role = item.get("role", "user")
            parts = []
            for part in item.get("content") or []:
                kind = part.get("type")
                if kind in ("input_text", "output_text", "text"):
                    parts.append({"type": "text", "text": part.get("text", "")})
                elif kind in ("input_image", "image_url"):
                    url = part.get("image_url") or part.get("url") or ""
                    image_url: dict[str, str] = {"url": url}
                    if part.get("detail"):
                        image_url["detail"] = part["detail"]
                    parts.append({"type": "image_url", "image_url": image_url})
            if role == "assistant":
                message: dict[str, Any] = {
                    "role": "assistant",
                    "content": "".join(part.get("text", "") for part in parts),
                }
                if item.get("reasoning_content"):
                    message["reasoning_content"] = item["reasoning_content"]
                messages.append(message)
            elif len(parts) == 1 and parts[0].get("type") == "text":
                messages.append({"role": role, "content": parts[0]["text"]})
            else:
                messages.append({"role": role, "content": parts})
        elif item_type == "function_call":
            tool_calls = []
            reasoning_content = ""
            content: str | None = None
            while index < len(items) and items[index].get("type") == "function_call":
                call = items[index]
                if not reasoning_content and call.get("reasoning_content"):
                    reasoning_content = call["reasoning_content"]
                if content is None and "content" in call:
                    content = call.get("content")
                tool_calls.append(
                    {
                        "id": call.get("call_id", ""),
                        "type": "function",
                        "function": {
                            "name": call.get("name", ""),
                            "arguments": call.get("arguments", "{}"),
                        },
                    }
                )
                index += 1
            message = {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            }
            if reasoning_content:
                message["reasoning_content"] = reasoning_content
            messages.append(message)
            continue
        elif item_type == "function_call_output":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.get("call_id", ""),
                    "content": item.get("output", ""),
                }
            )
        index += 1
    return messages


def _is_deepseek_model(model: str) -> bool:
    leaf = model.strip().lower().rsplit("/", 1)[-1]
    return leaf.startswith("deepseek")


def _deepseek_thinking(effort: str) -> tuple[dict[str, str], str | None]:
    """Map mjj effort onto DeepSeek thinking + reasoning_effort."""
    normalized = (effort or "high").strip().lower()
    if normalized in {"none", "minimal"}:
        return {"type": "disabled"}, None
    mapped = {
        "low": "low",
        "medium": "high",
        "high": "high",
        "xhigh": "high",
        "max": "max",
    }.get(normalized, "high")
    return {"type": "enabled"}, mapped


def probe(model: str = "auto", provider: str = "auto") -> dict:
    """One tiny round trip. Used by ``mjj auth status --probe`` and by the
    smoke test, so a broken credential fails loudly instead of at turn 40."""
    client = ModelClient(
        model=model,
        provider=provider,
        effort="low",
        summary="auto",
        resolver=CredentialResolver(provider=provider, model=model),
    )
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
    return {"ok": True, "model": model, "provider": provider,
            "text": "".join(text).strip()[:40],
            "usage": client.usage.summary()}
