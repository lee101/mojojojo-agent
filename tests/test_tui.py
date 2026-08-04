from __future__ import annotations

from types import SimpleNamespace

from mjj.agent import Agent
from mjj.model import ModelClient
from mjj.tools.base import Registry
from mjj.tui import InteractiveApp


def _args():
    return SimpleNamespace(
        auto_next_steps=False,
        auto_next_idea=False,
        auto_max_turns=0,
        disabled_tools=(),
        skill_paths=(),
    )


def test_slash_commands_update_live_model_controls(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    agent = Agent(
        registry=Registry(),
        client=ModelClient(model="auto", provider="auto", effort="medium"),
        cwd=tmp_path,
        instructions="test",
    )
    app = InteractiveApp(agent, _args())
    app.command("/effort max")
    app.command("/provider openrouter")
    app.command("/model openrouter/auto")
    assert agent.client.effort == "max"
    assert agent.client.provider == "openrouter"
    assert agent.client.model == "openrouter/auto"
    assert agent.client.resolver.provider == "openrouter"
    assert "effort: max" in capsys.readouterr().out


def test_auto_command_updates_live_autonomy_controls(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    agent = Agent(
        registry=Registry(),
        client=ModelClient(model="auto", provider="auto"),
        cwd=tmp_path,
        instructions="test",
    )
    args = _args()
    app = InteractiveApp(agent, args)

    app.command("/auto full 3")

    assert args.auto_next_steps is True
    assert args.auto_next_idea is True
    assert args.auto_max_turns == 3
    assert "autonomy: full" in capsys.readouterr().out


def test_tree_command_lists_branch_points(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    agent = Agent(
        registry=Registry(),
        client=ModelClient(model="auto", provider="auto"),
        cwd=tmp_path,
        instructions="test",
    )
    agent.items = [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "first prompt"}],
        }
    ]
    app = InteractiveApp(agent, _args())

    app.command("/tree")

    rendered = capsys.readouterr().out
    assert "first prompt" in rendered
    assert "/tree ITEM" in rendered
