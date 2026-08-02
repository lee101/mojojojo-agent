"""Responses request policy: compact cheaply and retry without surprises."""

from __future__ import annotations

import json

import pytest

from mjj.auth import Credential
from mjj.model import Event, ModelClient, ModelError, Usage, _decode_sse


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


def test_compaction_can_be_disabled():
    client = ModelClient(compact_threshold=0)
    body = client.request_body([], "brief", [], API_CREDENTIAL)
    assert "context_management" not in body


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
