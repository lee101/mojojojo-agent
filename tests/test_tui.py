from __future__ import annotations

from types import SimpleNamespace

from PIL import Image
from prompt_toolkit.document import Document

from mjj.agent import Agent, Step
from mjj.model import ModelClient
from mjj.tools import build_registry
from mjj.tools.base import Registry
from mjj.tools.shell import ShellTool
from mjj.tui import InteractiveApp, WorkspaceCompleter


def _args():
    return SimpleNamespace(
        auto_next_steps=False,
        auto_next_idea=False,
        auto_max_turns=0,
        disabled_tools=(),
        skill_paths=(),
        permission_mode="auto",
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


def test_workspace_completer_offers_slash_commands_and_fuzzy_files(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "water_shader.py").write_text("", encoding="utf-8")
    completer = WorkspaceCompleter(tmp_path)

    slash = list(completer.get_completions(Document("/perm"), None))
    files = list(completer.get_completions(Document("Review @water"), None))

    assert [item.text for item in slash] == ["/permissions"]
    assert [item.text for item in files] == ["src/water_shader.py"]


def test_workspace_completer_offers_images_for_preview_commands(tmp_path) -> None:
    (tmp_path / "art").mkdir()
    (tmp_path / "art" / "water result.webp").write_bytes(b"")
    (tmp_path / "art" / "notes.txt").write_text("")
    completer = WorkspaceCompleter(tmp_path)

    matches = list(
        completer.get_completions(Document("/preview water"), None)
    )

    assert [item.text for item in matches] == ['"art/water result.webp"']


def test_shell_shortcuts_control_whether_output_enters_context(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    agent = Agent(
        registry=Registry().add(ShellTool()),
        client=ModelClient(model="auto", provider="auto"),
        cwd=tmp_path,
        instructions="test",
    )
    app = InteractiveApp(agent, _args())

    app._shell("!printf included")
    included_items = len(agent.items)
    app._shell("!!printf local-only")

    assert included_items == 1
    assert len(agent.items) == 1
    assert "included" in str(agent.items[0])
    assert "local-only" not in str(agent.items[0])
    rendered = capsys.readouterr().out
    assert "included" in rendered and "local-only" in rendered


def test_permissions_review_init_and_status_commands(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    agent = Agent(
        registry=Registry().add(ShellTool()),
        client=ModelClient(model="auto", provider="auto"),
        cwd=tmp_path,
        instructions="test",
    )
    args = _args()
    app = InteractiveApp(agent, args)
    turns: list[str] = []
    monkeypatch.setattr(app, "turn", turns.append)

    app.command("/permissions read-only")
    app.command("/review focus on races")
    app.command("/init")
    app.command("/status")

    assert app.permission_policy.mode == "read-only"
    assert args.permission_mode == "read-only"
    assert "Do not modify files" in turns[0] and "races" in turns[0]
    assert "create a concise AGENTS.md" in turns[1]
    output = capsys.readouterr().out
    assert '"permissions": "read-only"' in output
    assert '"git":' in output


def test_init_does_not_overwrite_existing_agents_file(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    (tmp_path / "AGENTS.md").write_text("keep me", encoding="utf-8")
    agent = Agent(
        registry=Registry(),
        client=ModelClient(),
        cwd=tmp_path,
        instructions="test",
    )
    app = InteractiveApp(agent, _args())
    turns: list[str] = []
    monkeypatch.setattr(app, "turn", turns.append)

    app.command("/init")

    assert turns == []
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "keep me"
    assert "already exists" in capsys.readouterr().out


def test_undo_slash_command_restores_latest_patch(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MJJ_CHECKPOINT_ROOT", str(tmp_path / "checkpoints"))
    target = tmp_path / "value.txt"
    target.write_text("one\n")
    registry = build_registry(only=["patch", "checkpoint"])
    agent = Agent(
        registry=registry,
        client=ModelClient(),
        cwd=tmp_path,
        instructions="test",
    )
    registry.dispatch(
        "apply_patch",
        '{"input":"*** Begin Patch\\n*** Update File: value.txt\\n@@\\n-one\\n+two\\n*** End Patch"}',
        agent.ctx,
    )
    app = InteractiveApp(agent, _args())

    app.command("/undo")

    assert target.read_text() == "one\n"
    assert "restored" in capsys.readouterr().out


def test_image_command_previews_and_queues_attachment(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    source = tmp_path / "reference.png"
    Image.new("RGB", (12, 8), "purple").save(source)
    agent = Agent(registry=Registry(), cwd=tmp_path, instructions="test")
    app = InteractiveApp(agent, _args())
    rendered = []
    monkeypatch.setattr(
        "mjj.tui.render_terminal_image", lambda path: rendered.append(path)
    )

    app.command("/image reference.png")

    assert len(app.attachments) == 1
    assert rendered == [source]
    assert "attached reference.png" in capsys.readouterr().out


def test_tool_image_event_renders_in_response_chain(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    source = tmp_path / "result.webp"
    source.write_bytes(b"placeholder")
    agent = Agent(registry=Registry(), cwd=tmp_path, instructions="test")
    app = InteractiveApp(agent, _args())
    rendered = []
    monkeypatch.setattr(
        "mjj.tui.render_terminal_image",
        lambda path: rendered.append(path) or SimpleNamespace(ok=True),
    )
    monkeypatch.setattr("mjj.tui.print_formatted_text", lambda *args, **kwargs: None)

    app._render(
        [
            Step("tool_call", name="display_image", text='{"path":"result.webp"}'),
            Step(
                "tool_result",
                name="display_image",
                text="result.webp · 10×10",
                meta={"ok": True, "terminal_image": "result.webp"},
            ),
            Step("text", text="Done."),
        ]
    )

    assert rendered == [source]


def test_successful_mutating_tool_result_gets_compact_confirmation(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    agent = Agent(registry=Registry(), cwd=tmp_path, instructions="test")
    app = InteractiveApp(agent, _args())
    monkeypatch.setattr(
        "mjj.tui.print_formatted_text",
        lambda value, **kwargs: print(value.value, end=kwargs.get("end", "\n")),
    )

    app._render(
        [
            Step(
                "tool_result",
                name="check",
                text="all checks passed\nmore",
                meta={"ok": True},
            )
        ]
    )

    output = capsys.readouterr().out
    assert "✓ all checks passed more" in output
