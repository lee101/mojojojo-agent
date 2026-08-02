"""The turn loop, against a scripted model. No network."""

from __future__ import annotations

import json

from io import StringIO

from mjj.agent import Agent, Step, render_exec
from mjj.ledger import Ledger
from mjj.model import Event, ModelClient
from mjj.tools.base import Registry, ToolResult


class Echo:
    name = "echo"
    description = "echo text back"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def __init__(self):
        self.seen = []

    def run(self, args, ctx):
        self.seen.append(args)
        return ToolResult(output=ctx.ledger.clip("default", args["text"]))


class Boom:
    name = "boom"
    description = "always raises"
    parameters = {"type": "object", "properties": {}}

    def run(self, args, ctx):
        raise RuntimeError("kaboom")


def scripted(*turns):
    """Each turn is a list of output items; yields them as Responses events."""
    turns = list(turns)

    def stream(self, items, instructions, tools=None):
        for item in turns.pop(0):
            if item["type"] == "message":
                yield Event(
                    "response.output_text.delta",
                    {"delta": item["content"][0]["text"]},
                )
            yield Event("response.output_item.done", {"item": item})

    return stream


def message(text):
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


def call(name, args, call_id="c1"):
    return {
        "type": "function_call",
        "name": name,
        "arguments": json.dumps(args),
        "call_id": call_id,
    }


def build(monkeypatch, *turns, tools=None):
    monkeypatch.setattr(ModelClient, "stream", scripted(*turns))
    registry = Registry()
    for tool in tools or []:
        registry.add(tool)
    return Agent(registry=registry, ledger=Ledger())


def test_plain_answer_ends_the_loop(monkeypatch):
    agent = build(monkeypatch, [message("done")])
    kinds = [s.kind for s in agent.run("hi")]
    assert "text" in kinds
    assert kinds.count("usage") == 1


def test_tool_call_round_trip(monkeypatch):
    echo = Echo()
    agent = build(
        monkeypatch,
        [call("echo", {"text": "hello"})],
        [message("finished")],
        tools=[echo],
    )
    steps = list(agent.run("go"))
    assert echo.seen == [{"text": "hello"}]
    results = [s for s in steps if s.kind == "tool_result"]
    assert results[0].text == "hello" and results[0].meta["ok"]
    # The output item, the call, its output, and the final message are all in
    # the transcript we will resend next turn.
    types = [item["type"] for item in agent.items]
    assert types == ["message", "function_call", "function_call_output", "message"]


def test_program_issued_tool_call_preserves_caller(monkeypatch):
    issued = call("echo", {"text": "hello"})
    issued["caller"] = "program_1"
    agent = build(
        monkeypatch,
        [issued],
        [message("finished")],
        tools=[Echo()],
    )
    list(agent.run("go"))
    assert agent.items[2]["caller"] == "program_1"


def test_reasoning_items_are_echoed_verbatim(monkeypatch):
    reasoning = {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [],
        "encrypted_content": "OPAQUE",
    }
    agent = build(monkeypatch, [reasoning, message("ok")])
    list(agent.run("think"))
    assert agent.items[1] == reasoning  # [0] is the user message


def test_compaction_prunes_the_live_window(monkeypatch):
    compact = {"type": "compaction", "encrypted_content": "OPAQUE"}
    agent = build(monkeypatch, [compact, message("continued")])
    agent.items = [message("old answer")]
    steps = list(agent.run("keep going"))
    compacted = [step for step in steps if step.kind == "compaction"]
    assert compacted[0].meta["dropped_items"] == 2
    assert [item["type"] for item in agent.items] == ["compaction", "message"]


def test_tool_crash_becomes_a_failed_result_not_a_traceback(monkeypatch):
    agent = build(
        monkeypatch, [call("boom", {})], [message("recovered")], tools=[Boom()]
    )
    steps = list(agent.run("go"))
    failed = [s for s in steps if s.kind == "tool_result"][0]
    assert failed.meta["ok"] is False
    assert "kaboom" in failed.text


def test_unknown_tool_is_reported_to_the_model(monkeypatch):
    agent = build(monkeypatch, [call("nope", {})], [message("ok")])
    failed = [s for s in agent.run("go") if s.kind == "tool_result"][0]
    assert "unknown tool" in failed.text


def test_bad_json_arguments_do_not_kill_the_turn(monkeypatch):
    bad = {"type": "function_call", "name": "echo", "arguments": "{", "call_id": "c"}
    agent = build(monkeypatch, [bad], [message("ok")], tools=[Echo()])
    failed = [s for s in agent.run("go") if s.kind == "tool_result"][0]
    assert "valid JSON" in failed.text


def test_stream_error_is_surfaced_and_stops(monkeypatch):
    def explode(self, items, instructions, tools=None):
        raise RuntimeError("connection reset")
        yield  # pragma: no cover

    monkeypatch.setattr(ModelClient, "stream", explode)
    agent = Agent(registry=Registry())
    steps = list(agent.run("go"))
    assert steps[-1].kind == "error" and "connection reset" in steps[-1].text


def test_exec_renderer_keeps_progress_off_stdout() -> None:
    out, err = StringIO(), StringIO()
    steps = iter(
        [
            Step("text", text="working"),
            Step("tool_call", name="echo", text='{"text":"hi"}'),
            Step("usage", text="first usage"),
            Step("tool_result", name="echo", text="hi"),
            Step("text", text="done"),
            Step("usage", text="final usage"),
        ]
    )

    code, final = render_exec(steps, out, err)

    assert code == 0
    assert final == "done"
    assert out.getvalue() == "done\n"
    assert "· echo" in err.getvalue()
    assert "working" not in out.getvalue()


def test_exec_jsonl_is_parseable_and_returns_final_text() -> None:
    out, err = StringIO(), StringIO()
    code, final = render_exec(
        iter([Step("text", text="ok"), Step("usage", text="used")]),
        out,
        err,
        jsonl=True,
    )

    assert code == 0 and final == "ok"
    assert [json.loads(line)["type"] for line in out.getvalue().splitlines()] == [
        "text",
        "usage",
    ]
    assert err.getvalue() == ""
