from __future__ import annotations

import json
import stat
from types import SimpleNamespace

from mjj.agent import Agent
from mjj.goals import GoalStore, MAX_PROGRESS_ENTRIES
from mjj.model import Event, ModelClient
from mjj.tools.base import Registry
from mjj.tools.goal import GoalTool
from mjj.tui import InteractiveApp


def _message(text: str) -> dict:
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


def _call(action: str, message: str = "") -> dict:
    arguments = {"action": action}
    if message:
        arguments["message"] = message
    return {
        "type": "function_call",
        "name": "goal",
        "arguments": json.dumps(arguments),
        "call_id": f"goal-{action}",
    }


def _scripted(*turns):
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


def _args():
    return SimpleNamespace(
        auto_next_steps=False,
        auto_next_idea=False,
        auto_max_turns=1,
        disabled_tools=(),
        skill_paths=(),
        permission_mode="auto",
    )


def test_goal_store_is_workspace_scoped_atomic_and_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = GoalStore(workspace)

    goal = store.set("Ship the verified feature", session_id="session-1")
    for index in range(MAX_PROGRESS_ENTRIES + 5):
        goal = store.record(f"checkpoint {index}", evidence=f"test {index}")

    loaded = store.load()
    assert loaded is not None
    assert loaded.objective == "Ship the verified feature"
    assert loaded.session_id == "session-1"
    assert len(loaded.progress) == MAX_PROGRESS_ENTRIES
    assert loaded.progress[0]["message"] == "checkpoint 5"
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_corrupt_goal_state_degrades_to_no_goal(tmp_path, monkeypatch):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    store = GoalStore(tmp_path)
    store.path.write_text("{broken", encoding="utf-8")

    assert store.load() is None


def test_goal_tool_requires_evidence_message_before_completion(tmp_path, monkeypatch):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    store = GoalStore(tmp_path)
    store.set("Finish safely")
    agent = Agent(registry=Registry().add(GoalTool()), cwd=tmp_path, instructions="test")
    agent.bind_goal_store(store)

    denied = agent.registry.dispatch("goal", '{"action":"complete"}', agent.ctx)
    completed = agent.registry.dispatch(
        "goal",
        '{"action":"complete","message":"pytest passed"}',
        agent.ctx,
    )

    assert denied.ok is False
    assert completed.ok is True
    assert store.load().status == "complete"


def test_active_goal_continues_then_stops_when_model_marks_complete(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    store = GoalStore(tmp_path)
    store.set("Make the router tests pass")
    monkeypatch.setattr(
        ModelClient,
        "stream",
        _scripted(
            [_message("first checkpoint")],
            [_call("complete", "router tests passed")],
            [_message("verified and complete")],
        ),
    )
    agent = Agent(
        registry=Registry(),
        client=ModelClient(),
        cwd=tmp_path,
        instructions="test",
        goal_store=store,
    )

    steps = list(agent.run("start", max_autonomous_turns=3))

    assert store.load().status == "complete"
    assert [step.meta["status"] for step in steps if step.kind == "goal"] == [
        "active",
        "complete",
    ]
    first_user = agent.items[0]["content"][0]["text"]
    assert "Make the router tests pass" in first_user
    assert "Call goal complete only after" in first_user
    assert "goal" not in agent.registry.tools


def test_goal_turn_budget_checkpoints_without_discarding_goal(tmp_path, monkeypatch):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    store = GoalStore(tmp_path)
    store.set("Keep improving")
    monkeypatch.setattr(
        ModelClient,
        "stream",
        _scripted([_message("one")], [_message("two")]),
    )
    agent = Agent(
        registry=Registry(),
        client=ModelClient(),
        cwd=tmp_path,
        instructions="test",
        goal_store=store,
    )

    steps = list(agent.run("start", max_autonomous_turns=1))

    assert store.load().status == "active"
    assert [step.meta["status"] for step in steps if step.kind == "goal"] == [
        "active",
        "checkpoint",
    ]


def test_tui_goal_command_persists_and_starts_the_objective(tmp_path, monkeypatch):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    agent = Agent(registry=Registry(), cwd=tmp_path, instructions="test")
    app = InteractiveApp(agent, _args())
    turns: list[str] = []
    monkeypatch.setattr(app, "turn", turns.append)

    app.command("/goal Make every focused test pass")

    assert agent.current_goal().objective == "Make every focused test pass"
    assert "goal" in agent.registry.tools
    assert turns == ["Begin the active goal now and establish its first checkpoint."]
