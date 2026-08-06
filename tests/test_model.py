"""Responses request policy: compact cheaply and retry without surprises."""

from __future__ import annotations

import json

import pytest

from mjj.auth import Credential
from mjj.model import Event, ModelClient, ModelError, Usage, _decode_sse
from mjj.model import _chat_messages


API_CREDENTIAL = Credential("api_key", "sk-test", "https://example.test/v1")


class Resolver:
    def __init__(self):
        self.calls = []

    def resolve(self, force=False, fallback=False):
        self.calls.append((force, fallback))
        return API_CREDENTIAL


def test_request_enables_server_side_compaction():
    client = ModelClient(compact_threshold=12345)
    body = client.request_body([], "brief", [], API_CREDENTIAL)
    assert body["context_management"] == [
        {"type": "compaction", "compact_threshold": 12345}
    ]
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["text"] == {"verbosity": "low"}


def test_request_profiles_codex_prompt_from_effective_model():
    credential = Credential(
        "api_key",
        "sk-test",
        "https://example.test/v1",
        default_model="gpt-5.3-codex",
    )

    body = ModelClient(model="auto").request_body([], "brief", [], credential)

    assert "For Codex models" in body["instructions"]


def test_compaction_can_be_disabled():
    client = ModelClient(compact_threshold=0)
    body = client.request_body([], "brief", [], API_CREDENTIAL)
    assert "context_management" not in body


def test_output_token_ceiling_is_sent_to_both_api_shapes():
    credential = Credential(
        "api_key",
        "test",
        "https://example.test/v1",
        provider="openpaths",
        api_style="chat_completions",
    )
    client = ModelClient(max_output_tokens=321)

    responses = client.request_body([], "brief", [], API_CREDENTIAL)
    chat = client.chat_request_body([], "brief", [], credential)

    assert responses["max_output_tokens"] == 321
    assert chat["max_tokens"] == 321


def test_unsupported_compaction_degrades_to_plain_request(monkeypatch):
    client = ModelClient(resolver=Resolver(), max_retries=1)
    bodies = []

    def once(_credential, body):
        bodies.append(body)
        if len(bodies) == 1:
            raise ModelError(
                "HTTP 400: unknown context_management field", status=400
            )
        yield Event("response.completed", {"response": {"usage": {}}})

    monkeypatch.setattr(client, "_stream_once", once)
    list(client.stream([], "brief"))
    assert "context_management" in bodies[0]
    assert "context_management" not in bodies[1]


def test_unsupported_cache_controls_degrade_to_compatible_request(monkeypatch):
    client = ModelClient(
        resolver=Resolver(),
        model="gpt-5.6-terra",
        cache_mode="explicit",
        max_retries=1,
    )
    bodies = []

    def once(_credential, body):
        bodies.append(body)
        if len(bodies) == 1:
            raise ModelError(
                "HTTP 400: unknown prompt_cache_options field",
                status=400,
            )
        yield Event("response.completed", {"response": {"usage": {}}})

    monkeypatch.setattr(client, "_stream_once", once)
    list(client.stream([], "stable " * 1000))

    assert "prompt_cache_options" in bodies[0]
    assert "prompt_cache_options" not in bodies[1]


def test_known_model_uses_closest_supported_reasoning_effort():
    client = ModelClient(model="gpt-5.6-sol", effort="minimal")

    body = client.request_body([], "brief", [], API_CREDENTIAL)

    assert body["reasoning"]["effort"] == "low"
    assert client.effort == "minimal"
    assert client.last_effective_effort == "low"


def test_unsupported_effort_is_learned_and_retried_transparently(monkeypatch):
    client = ModelClient(
        resolver=Resolver(), model="future-reasoner", effort="minimal", max_retries=0
    )
    bodies = []

    def once(_credential, body):
        bodies.append(body)
        if len(bodies) == 1:
            raise ModelError(
                "HTTP 400: Unsupported value for reasoning.effort. "
                "Supported values are: 'none', 'low', 'medium', and 'high'.",
                status=400,
            )
        yield Event("response.completed", {"response": {"usage": {}}})

    monkeypatch.setattr(client, "_stream_once", once)
    events = list(client.stream([], "brief"))

    assert [body["reasoning"]["effort"] for body in bodies] == ["minimal", "low"]
    assert [event.type for event in events] == [
        "mjj.effort_adjusted",
        "response.completed",
    ]
    assert events[0].data["requested"] == "minimal"
    assert events[0].data["effective"] == "low"


def test_transient_retry_does_not_refresh_credentials(monkeypatch):
    resolver = Resolver()
    client = ModelClient(resolver=resolver, max_retries=1)
    calls = 0

    def once(_credential, _body):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelError("HTTP 429", status=429, retryable=True)
        yield Event("response.completed", {"response": {"usage": {}}})

    monkeypatch.setattr(client, "_stream_once", once)
    monkeypatch.setattr("mjj.model.time.sleep", lambda _seconds: None)
    list(client.stream([], "brief"))
    assert resolver.calls == [(False, False), (False, False)]


def test_retry_status_and_request_fallback_when_stream_never_starts(monkeypatch):
    client = ModelClient(resolver=Resolver(), max_retries=1)
    calls = []

    def stream_once(_credential, _body):
        raise ModelError("stream transport timeout", retryable=True)
        yield  # pragma: no cover

    def request_once(_credential, body):
        calls.append(body["stream"])
        yield Event("response.completed", {"response": {"usage": {}}})

    monkeypatch.setattr(client, "_stream_once", stream_once)
    monkeypatch.setattr(client, "_request_once", request_once)
    monkeypatch.setattr("mjj.model.time.sleep", lambda _seconds: None)

    events = list(client.stream([], "brief"))

    assert [event.type for event in events] == [
        "mjj.retry",
        "mjj.request_fallback",
        "response.completed",
    ]
    assert calls == [True]


def test_partial_stream_is_never_replayed_as_a_request(monkeypatch):
    client = ModelClient(resolver=Resolver(), max_retries=4)
    calls = 0

    def stream_once(_credential, _body):
        nonlocal calls
        calls += 1
        yield Event("response.output_text.delta", {"delta": "started"})
        raise ModelError("connection lost", retryable=True)

    monkeypatch.setattr(client, "_stream_once", stream_once)

    with pytest.raises(ModelError, match="connection lost"):
        list(client.stream([], "brief"))
    assert calls == 1


def test_repeated_401_refreshes_then_falls_back(monkeypatch):
    resolver = Resolver()
    client = ModelClient(resolver=resolver, max_retries=3)
    calls = 0

    def once(_credential, _body):
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise ModelError("HTTP 401", status=401, retryable=True)
        yield Event("response.completed", {"response": {"usage": {}}})

    monkeypatch.setattr(client, "_stream_once", once)
    monkeypatch.setattr("mjj.model.time.sleep", lambda _seconds: None)
    list(client.stream([], "brief"))
    assert resolver.calls == [(False, False), (True, False), (False, True)]


def test_usage_tracks_cache_reads_and_writes():
    usage = Usage()
    usage.add(
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {
                "cached_tokens": 60,
                "cache_write_tokens": 30,
            },
        }
    )
    assert usage.cached_input_tokens == 60
    assert usage.cache_write_tokens == 30
    assert "60% cached" in usage.summary()
    assert "30 cache write" in usage.summary()


def test_openai_56_cache_policy_marks_only_a_reused_stable_prefix():
    instructions = "stable instructions " * 300
    tools = [{"type": "function", "name": "read", "description": "read"}]
    client = ModelClient(model="gpt-5.6-terra", cache_mode="auto")

    cold = client.request_body([], instructions, tools, API_CREDENTIAL)
    warm = client.request_body([], instructions, tools, API_CREDENTIAL)

    assert cold["prompt_cache_options"] == {"mode": "explicit"}
    assert cold["input"] == []
    assert warm["prompt_cache_options"] == {"mode": "explicit", "ttl": "30m"}
    assert warm["input"][0]["role"] == "developer"
    assert warm["input"][0]["content"][0]["prompt_cache_breakpoint"] == {
        "mode": "explicit"
    }
    assert warm["prompt_cache_key"].startswith("mjj:")


def test_openai_56_implicit_cache_keeps_a_stable_routing_key():
    client = ModelClient(model="gpt-5.6-terra", cache_mode="implicit")

    body = client.request_body([], "stable instructions " * 300, [], API_CREDENTIAL)

    assert body["prompt_cache_options"] == {"mode": "implicit"}
    assert body["prompt_cache_key"].startswith("mjj:")


def test_cache_off_disables_writes_without_a_breakpoint_or_session_key():
    client = ModelClient(
        model="gpt-5.6-terra",
        cache_mode="off",
        cache_key="configured-session-key",
    )

    body = client.request_body(
        [],
        "stable instructions " * 300,
        [],
        API_CREDENTIAL,
    )

    # GPT-5.6 needs explicit mode with no breakpoint to opt out of its
    # implicit write. The empty input proves no cache boundary was inserted.
    assert body["prompt_cache_options"] == {"mode": "explicit"}
    assert "prompt_cache_key" not in body
    assert body["input"] == []


def test_transport_retry_does_not_look_like_prefix_reuse(monkeypatch):
    resolver = Resolver()
    client = ModelClient(
        model="gpt-5.6-terra",
        cache_mode="auto",
        resolver=resolver,
        max_retries=1,
    )
    bodies = []

    def retry_once(_credential, body):
        bodies.append(body)
        if len(bodies) == 1:
            raise ModelError("HTTP 429", status=429, retryable=True)
        yield Event("response.completed", {"response": {"usage": {}}})

    monkeypatch.setattr(client, "_stream_once", retry_once)
    monkeypatch.setattr("mjj.model.time.sleep", lambda _seconds: None)

    list(client.stream([], "stable instructions " * 300))

    assert len(bodies) == 2
    assert all("prompt_cache_key" not in body for body in bodies)


def test_anthropic_chat_request_gets_adaptive_cache_control():
    credential = Credential(
        "api_key",
        "op-test",
        "https://example.test/v1",
        provider="openpaths",
        api_style="chat_completions",
    )
    client = ModelClient(model="claude-sonnet-4-6", cache_mode="auto")
    instructions = "stable instructions " * 300

    body = client.chat_request_body([], instructions, [], credential)

    system = body["messages"][0]["content"][0]
    assert system["cache_control"] == {"type": "ephemeral", "ttl": "5m"}


def test_custom_chat_gateway_does_not_receive_anthropic_cache_extensions():
    credential = Credential(
        "api_key",
        "custom-test",
        "https://example.test/v1",
        provider="custom",
        api_style="chat_completions",
    )
    client = ModelClient(model="claude-compatible", cache_mode="explicit")

    body = client.chat_request_body(
        [], "stable instructions " * 300, [], credential
    )

    assert isinstance(body["messages"][0]["content"], str)


def test_chat_usage_normalizes_anthropic_cache_token_names():
    from mjj.model import _chat_usage

    normalized = _chat_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 12,
            "cache_read_input_tokens": 70,
            "cache_creation_input_tokens": 20,
        }
    )

    assert normalized["input_tokens_details"] == {
        "cached_tokens": 70,
        "cache_write_tokens": 20,
    }


def test_sse_decoder_handles_multiline_records_and_eof():
    stream = [
        b"event: response.output_text.delta\n",
        b'data: {"type":"response.output_text.delta",\n',
        b'data: "delta":"hello"}\n',
        b"\n",
        b'data: {"type":"response.completed"}\n',
    ]
    events = list(_decode_sse(stream))
    assert [(event.type, event.delta) for event in events] == [
        ("response.output_text.delta", "hello"),
        ("response.completed", ""),
    ]


def test_incomplete_response_records_usage_before_error(monkeypatch):
    client = ModelClient()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            document = {
                "type": "response.incomplete",
                "response": {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "usage": {"input_tokens": 12, "output_tokens": 3},
                },
            }
            yield f"data: {json.dumps(document)}\n".encode()
            yield b"\n"

    monkeypatch.setattr("mjj.model.urllib.request.urlopen", lambda *_a, **_k: Response())
    with pytest.raises(ModelError, match="max_output_tokens"):
        list(client._stream_once(API_CREDENTIAL, {}))
    assert (client.usage.input_tokens, client.usage.output_tokens) == (12, 3)


def test_chat_request_translates_responses_tools_and_images():
    credential = Credential(
        "api_key",
        "op-test",
        "https://example.test/v1",
        provider="openpaths",
        api_style="chat_completions",
        default_model="openpaths/auto-code",
    )
    client = ModelClient(model="auto", effort="high")
    items = [
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "inspect"},
                {"type": "input_image", "image_url": "data:image/webp;base64,eA=="},
            ],
        }
    ]
    body = client.chat_request_body(
        items,
        "system",
        [{"type": "function", "name": "read", "description": "Read", "parameters": {}}],
        credential,
    )
    assert body["model"] == "openpaths/auto-code"
    assert body["reasoning_effort"] == "high"
    assert body["messages"][1]["content"][1]["type"] == "image_url"
    assert body["tools"][0]["function"]["name"] == "read"


def test_chat_request_profiles_openrouter_grok_prompt():
    credential = Credential(
        "api_key",
        "or-test",
        "https://example.test/v1",
        provider="openrouter",
        api_style="chat_completions",
    )

    body = ModelClient(model="x-ai/grok-4.5").chat_request_body(
        [], "brief", [], credential
    )

    assert body["model"] == "x-ai/grok-4.5"
    assert "For Grok models" in body["messages"][0]["content"]


def test_chat_stream_is_normalized_to_agent_events(monkeypatch):
    credential = Credential(
        "api_key", "op-test", "https://example.test/v1",
        provider="openpaths", api_style="chat_completions",
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            chunks = [
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "read", "arguments": '{"path":'}}]}}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"README.md"}'}}]}}]},
                {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 3}},
            ]
            for chunk in chunks:
                yield f"data: {json.dumps(chunk)}\n".encode()
                yield b"\n"
            yield b"data: [DONE]\n"
            yield b"\n"

    monkeypatch.setattr("mjj.model.urllib.request.urlopen", lambda *_a, **_k: Response())
    events = list(ModelClient()._stream_chat_once(credential, {"model": "x"}))
    calls = [event.item for event in events if event.type == "response.output_item.done"]
    assert calls == [
        {
            "type": "function_call",
            "call_id": "c1",
            "name": "read",
            "arguments": '{"path":"README.md"}',
            "content": "",
        }
    ]


def test_chat_stream_keeps_reasoning_content_for_tool_replay(monkeypatch):
    credential = Credential(
        "api_key",
        "ds-test",
        "https://api.deepseek.com",
        provider="deepseek",
        api_style="chat_completions",
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            chunks = [
                {"choices": [{"delta": {"reasoning_content": "need list then search"}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "content": "checking",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "c1",
                                        "function": {
                                            "name": "list",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                {"choices": [], "usage": {"prompt_tokens": 8, "completion_tokens": 4}},
            ]
            for chunk in chunks:
                yield f"data: {json.dumps(chunk)}\n".encode()
                yield b"\n"
            yield b"data: [DONE]\n"
            yield b"\n"

    monkeypatch.setattr("mjj.model.urllib.request.urlopen", lambda *_a, **_k: Response())
    events = list(
        ModelClient()._stream_chat_once(credential, {"model": "deepseek-v4-flash"})
    )
    assert [
        event.delta
        for event in events
        if event.type == "response.reasoning_summary_text.delta"
    ] == ["need list then search"]
    calls = [event.item for event in events if event.type == "response.output_item.done"]
    assert calls == [
        {
            "type": "function_call",
            "call_id": "c1",
            "name": "list",
            "arguments": "{}",
            "reasoning_content": "need list then search",
            "content": "checking",
        }
    ]


def test_deepseek_chat_body_enables_thinking_and_maps_effort():
    credential = Credential(
        "api_key",
        "ds-test",
        "https://api.deepseek.com",
        provider="deepseek",
        api_style="chat_completions",
        default_model="deepseek-v4-flash",
    )
    body = ModelClient(model="deepseek-v4-flash", effort="medium").chat_request_body(
        [], "system", [], credential
    )
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "high"

    disabled = ModelClient(model="deepseek-v4-flash", effort="none").chat_request_body(
        [], "system", [], credential
    )
    assert disabled["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in disabled


def test_chat_history_groups_parallel_calls_before_tool_outputs():
    messages = _chat_messages(
        [
            {"type": "function_call", "call_id": "a", "name": "read", "arguments": "{}"},
            {"type": "function_call", "call_id": "b", "name": "list", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "a", "output": "one"},
            {"type": "function_call_output", "call_id": "b", "output": "two"},
        ],
        "system",
    )
    assert [message["role"] for message in messages] == ["system", "assistant", "tool", "tool"]
    assert [call["id"] for call in messages[1]["tool_calls"]] == ["a", "b"]


def test_chat_history_echoes_deepseek_reasoning_on_tool_turns():
    messages = _chat_messages(
        [
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "search",
                "arguments": '{"query":"three"}',
                "reasoning_content": "look for three.js first",
                "content": "searching",
            },
            {
                "type": "function_call_output",
                "call_id": "c1",
                "output": "no matches",
            },
        ],
        "system",
    )
    assert messages[1] == {
        "role": "assistant",
        "content": "searching",
        "reasoning_content": "look for three.js first",
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {
                    "name": "search",
                    "arguments": '{"query":"three"}',
                },
            }
        ],
    }
