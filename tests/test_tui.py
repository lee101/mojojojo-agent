from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from mjj.agent import Agent, Step
from mjj.model import ModelClient
from mjj.tools import build_registry
from mjj.tools.base import Registry
from mjj.tools.shell import ShellTool
from mjj.tui import (
    DistinctFileHistory,
    InteractiveApp,
    WorkspaceCompleter,
    _model_choices,
    _pick_model,
    _workspace_history_path,
)


def _args():
    return SimpleNamespace(
        auto_next_steps=False,
        auto_next_idea=False,
        auto_max_turns=0,
        disabled_tools=(),
        skill_paths=(),
        plugins=(),
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
    output = capsys.readouterr().out
    assert "autonomy: full" in output and "max turns: 3" in output


def test_plan_mode_is_read_only_until_explicit_approval(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    agent = Agent(registry=Registry(), cwd=tmp_path, instructions="test")
    args = _args()
    app = InteractiveApp(agent, args)
    turns = []
    monkeypatch.setattr(app, "turn", turns.append)

    app.command("/plan replace the parser")

    assert app.permission_policy.mode == "read-only"
    assert args.permission_mode == "read-only"
    assert agent.ctx.state["plan-permission-mode"] == "auto"
    assert "Plan this task" in turns[-1]

    app.command("/plan approve")

    assert app.permission_policy.mode == "auto"
    assert args.permission_mode == "auto"
    assert "Implement it completely" in turns[-1]
    assert "plan ready" in capsys.readouterr().out


def test_manual_compaction_restores_automatic_threshold(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    agent = Agent(registry=Registry(), cwd=tmp_path, instructions="test")
    app = InteractiveApp(agent, _args())
    original = agent.client.compact_threshold
    observed = []
    monkeypatch.setattr(
        app,
        "turn",
        lambda _prompt: observed.append(agent.client.compact_threshold),
    )

    app.command("/compact")

    assert observed == [1]
    assert agent.client.compact_threshold == original


def test_raw_command_controls_expanded_tool_output(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    app = InteractiveApp(
        Agent(registry=Registry(), cwd=tmp_path, instructions="test"), _args()
    )

    app.command("/raw on")
    assert app.transcript_expanded is True
    app.command("/raw off")
    assert app.transcript_expanded is False
    app.command("/raw maybe")
    assert "value must be on, off, or toggle" in capsys.readouterr().out


def test_loop_forever_is_an_unlimited_full_autonomy_alias(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    agent = Agent(registry=Registry(), cwd=tmp_path, instructions="test")
    args = _args()
    args.auto_max_turns = 7
    app = InteractiveApp(agent, args)

    app.command("/loop forever")

    assert args.auto_next_steps is True
    assert args.auto_next_idea is True
    assert args.auto_max_turns == 0
    output = capsys.readouterr().out
    assert "autonomy: full" in output and "max turns: unlimited" in output


def test_prompt_history_is_private_distinct_and_scoped_by_directory(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    legacy = tmp_path / "home" / "history"
    legacy.write_text("old global prompt\n", encoding="utf-8")
    first = _workspace_history_path(tmp_path / "one")
    second = _workspace_history_path(tmp_path / "two")
    history = DistinctFileHistory(str(first))

    history.append_string("same prompt")
    history.append_string("same prompt")
    history.append_string("another prompt")

    assert first != second
    assert first.parent == second.parent == tmp_path / "home" / "prompt-history"
    if os.name != "nt":
        assert first.stat().st_mode & 0o777 == 0o600
        assert first.parent.stat().st_mode & 0o777 == 0o700
    assert first.read_text(encoding="utf-8").count("same prompt") == 1
    assert legacy.read_text(encoding="utf-8") == "old global prompt\n"


def test_unavailable_prompt_history_degrades_to_memory(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))

    def unavailable(cwd):
        raise PermissionError("history denied")

    monkeypatch.setattr("mjj.tui._workspace_history_path", unavailable)
    agent = Agent(registry=Registry(), cwd=tmp_path, instructions="test")
    app = InteractiveApp(agent, _args())

    app._welcome()

    assert "prompt history is in-memory" in app.history_warning
    assert "history denied" in capsys.readouterr().out


def test_prompt_history_skips_large_entries_and_trims_at_entry_boundaries(
    tmp_path, monkeypatch
):
    path = tmp_path / "history"
    path.touch()
    monkeypatch.setattr("mjj.tui.MAX_HISTORY_ENTRY_CHARS", 40)
    monkeypatch.setattr("mjj.tui.MAX_HISTORY_BYTES", 220)
    history = DistinctFileHistory(str(path))

    history.append_string("x" * 41)
    for index in range(12):
        history.append_string(f"prompt {index:02d} with text")

    content = path.read_text(encoding="utf-8")
    loaded = list(DistinctFileHistory(str(path)).load_history_strings())
    assert path.stat().st_size <= 220
    assert "x" * 41 not in content
    assert "prompt 11 with text" in loaded
    assert "prompt 00 with text" not in loaded


def test_prompt_history_append_failure_does_not_end_the_session(
    tmp_path, monkeypatch
):
    path = tmp_path / "history"
    path.touch()
    history = DistinctFileHistory(str(path))

    def disk_full(self, string):
        raise OSError("disk full")

    monkeypatch.setattr("prompt_toolkit.history.FileHistory.append_string", disk_full)

    history.append_string("keep running")

    assert path.read_text(encoding="utf-8") == ""


def test_cache_command_updates_policy_and_reports_telemetry(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    agent = Agent(
        registry=Registry(),
        client=ModelClient(cache_mode="auto"),
        cwd=tmp_path,
        instructions="test",
    )
    app = InteractiveApp(agent, _args())

    app.command("/cache off")
    app.command("/cache auto")

    assert agent.client.cache_mode == "auto"
    output = capsys.readouterr().out
    assert '"mode": "off"' in output
    assert '"cache_read_tokens": 0' in output


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


def test_workspace_completer_offers_live_command_values(tmp_path) -> None:
    completer = WorkspaceCompleter(tmp_path, provider="openai")
    openpaths = WorkspaceCompleter(tmp_path, provider="openpaths")
    openrouter = WorkspaceCompleter(tmp_path, provider="openrouter")
    auto_completer = WorkspaceCompleter(tmp_path)

    model = list(completer.get_completions(Document("/model terra"), None))
    grok = list(openpaths.get_completions(Document("/model grok"), None))
    routed_grok = list(
        openrouter.get_completions(Document("/model grok"), None)
    )
    effort = list(completer.get_completions(Document("/reasoning xh"), None))
    provider = list(
        completer.get_completions(Document("/provider router"), None)
    )
    permissions = list(
        completer.get_completions(Document("/permissions read"), None)
    )
    auto_model = list(
        auto_completer.get_completions(Document("/model sol"), None)
    )
    described = list(
        completer.get_completions(Document("/sign"), None)
    )
    cache = list(completer.get_completions(Document("/cache exp"), None))

    assert [item.text for item in model] == ["gpt-5.6-terra"]
    assert [item.text for item in grok] == ["grok-4.5"]
    assert [item.text for item in routed_grok] == ["x-ai/grok-4.5"]
    assert [item.text for item in effort] == ["xhigh"]
    assert [item.text for item in provider] == ["openrouter"]
    assert [item.text for item in permissions] == ["read-only"]
    assert [item.text for item in auto_model] == ["gpt-5.6-sol"]
    assert [item.text for item in described] == ["/login"]
    assert [item.text for item in cache] == ["explicit"]


def test_model_selection_accepts_number_substring_and_relative_change(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    agent = Agent(
        registry=Registry(),
        client=ModelClient(
            model="auto",
            provider="openai",
            effort="medium",
            verbosity="low",
        ),
        cwd=tmp_path,
        instructions="test",
    )
    app = InteractiveApp(agent, _args())

    app.command("/model 2")
    assert agent.client.model == "gpt-5.6-sol"
    assert 'model = "gpt-5.6-sol"' in (tmp_path / "home" / "config.toml").read_text(
        encoding="utf-8"
    )
    app.command("/model terra")
    assert agent.client.model == "gpt-5.6-terra"
    assert 'model = "gpt-5.6-terra"' in (tmp_path / "home" / "config.toml").read_text(
        encoding="utf-8"
    )
    app.command("/model prev")
    assert agent.client.model == "gpt-5.6-sol"
    app.command("/model gpt")
    assert agent.client.model == "gpt-5.6-sol"
    app.command("/reasoning next")
    assert agent.client.effort == "high"
    app.command("/verbosity next")
    assert agent.client.verbosity == "medium"

    app.command("/model")
    output = capsys.readouterr().out
    assert "saved default model" in output
    assert "* 2. gpt-5.6-sol" in output
    assert "ambiguous model" in output
    assert "/model NUMBER|NAME|next|prev" in output


def test_model_picker_moves_with_arrows_and_accepts_enter() -> None:
    with create_pipe_input() as pipe:
        pipe.send_text("\x1b[B\r")
        selected = _pick_model(
            ("auto", "auto-code", "auto-best"),
            "auto",
            "auto",
            input_stream=pipe,
            output=DummyOutput(),
        )

    assert selected == "auto-code"


def test_auto_model_picker_prioritizes_stable_task_aliases() -> None:
    assert _model_choices("auto")[:5] == (
        "auto",
        "auto-code",
        "auto-fast",
        "auto-cheap",
        "auto-best",
    )


def test_workspace_files_are_discovered_only_when_file_completion_is_used(
    tmp_path, monkeypatch
) -> None:
    calls = []
    monkeypatch.setattr(
        "mjj.tui.discover_project_files",
        lambda cwd: calls.append(Path(cwd)) or ("app.py",),
    )
    completer = WorkspaceCompleter(tmp_path)

    list(completer.get_completions(Document("/mo"), None))
    assert calls == []
    list(completer.get_completions(Document("@app"), None))
    assert calls == [tmp_path]


def test_shift_arrows_change_reasoning_and_toolbar_shows_live_state(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    agent = Agent(
        registry=Registry(),
        client=ModelClient(model="auto-best", provider="auto", effort="medium"),
        cwd=tmp_path,
        instructions="test",
    )
    app = InteractiveApp(agent, _args())
    bindings = {
        tuple(getattr(key, "value", key) for key in binding.keys): binding.handler
        for binding in app.bindings.bindings
    }
    event = SimpleNamespace(app=SimpleNamespace(invalidate=lambda: None))

    bindings[("s-up",)](event)
    assert agent.client.effort == "high"
    bindings[("s-down",)](event)
    assert agent.client.effort == "medium"
    toolbar = app._toolbar().value
    assert "model <b>auto-best</b>" in toolbar
    assert "reasoning <b>medium</b>" in toolbar
    assert "Shift+↑/↓ reasoning" in toolbar
    agent.client.model = "auto"
    app._cycle_model(1)
    assert agent.client.model == "auto-code"


def test_tool_results_have_compact_preview_and_ctrl_t_full_transcript(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        "mjj.tui.print_formatted_text",
        lambda value, end="\n": print(str(value), end=end),
    )
    app = InteractiveApp(
        Agent(registry=Registry(), cwd=tmp_path, instructions="test"), _args()
    )
    steps = [
        Step(kind="tool_call", name="shell", text='{"command":["pytest","-q"]}'),
        Step(kind="tool_result", name="shell", text="one\ntwo\nthree\nfour"),
    ]

    app._render(iter(steps))
    compact = capsys.readouterr().out
    assert "pytest -q" in compact
    assert "↳" not in compact
    assert "one" in compact and "… 1 more lines" in compact
    assert "four" not in compact

    app.transcript_expanded = True
    app._show_activity()
    assert "four" in capsys.readouterr().out


def test_live_enter_steers_while_tab_queues_a_follow_up(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        "mjj.tui.print_formatted_text",
        lambda value, end="\n": print(str(value), end=end),
    )
    agent = Agent(registry=Registry(), cwd=tmp_path, instructions="test")
    app = InteractiveApp(agent, _args())

    assert app._accept_live_input("fix this first", queued=False)
    assert agent.steering.get_nowait() == "fix this first"
    assert app._accept_live_input("then document it", queued=True)
    assert app.pending_prompts == ["then document it"]
    output = capsys.readouterr().out
    assert "steering sent" in output
    assert "follow-up queued (1)" in output


def test_help_and_hotkey_aliases_report_the_live_surface(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    agent = Agent(registry=Registry(), cwd=tmp_path, instructions="test")
    app = InteractiveApp(agent, _args())

    app.command("/commands")
    app.command("/keys")
    app.command("/tips")

    output = capsys.readouterr().out
    assert "/model" in output and "/reasoning" in output
    assert "F2 or Alt+M" in output
    assert "F3 or Alt+R" in output
    assert "F4 or Alt+V" in output
    assert "Tab queues a separate follow-up" in output
    assert "Enter sends guidance into the active task" in output


def test_render_finishes_with_visible_ready_time_and_usage(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        "mjj.tui.print_formatted_text",
        lambda value, end="\n": print(str(value), end=end),
    )
    app = InteractiveApp(
        Agent(registry=Registry(), cwd=tmp_path, instructions="test"), _args()
    )

    app._render(
        iter(
            [
                Step(kind="text", text="Done."),
                Step(kind="usage", text="1 req · in 10 · out 4", meta={"seconds": 1.25}),
            ]
        )
    )

    output = capsys.readouterr().out
    assert "ready · 1.25s · 1 req · in 10 · out 4" in output


def test_plan_and_mcp_commands_show_live_tool_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    registry = Registry(warnings=["MCP broken: unavailable"])
    registry.tools["mcp__browser__open"] = SimpleNamespace()
    agent = Agent(registry=registry, cwd=tmp_path, instructions="test")
    agent.ctx.state["plan"] = {
        "explanation": "verify parity",
        "plan": [{"step": "Run tests", "status": "in_progress"}],
    }
    app = InteractiveApp(agent, _args())

    app.command("/plan")
    app.command("/mcp")
    app.command("/plan clear")

    output = capsys.readouterr().out
    assert '"step": "Run tests"' in output
    assert "mcp__browser__open" in output
    assert "MCP broken: unavailable" in output
    assert "structured plan cleared" in output
    assert "plan" not in agent.ctx.state


def test_provider_change_resets_incompatible_known_model(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    agent = Agent(
        registry=Registry(),
        client=ModelClient(model="gpt-5.6-sol", provider="openai"),
        cwd=tmp_path,
        instructions="test",
    )
    app = InteractiveApp(agent, _args())

    app.command("/provider openrouter")

    assert agent.client.provider == "openrouter"
    assert agent.client.model == "auto"
    assert agent.client.resolver.provider == "openrouter"


def test_portable_model_reasoning_and_verbosity_bindings_are_registered(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    agent = Agent(registry=Registry(), cwd=tmp_path, instructions="test")
    app = InteractiveApp(agent, _args())
    sequences = {
        tuple(getattr(key, "value", key) for key in binding.keys)
        for binding in app.bindings.bindings
    }

    assert ("f2",) in sequences and ("escape", "m") in sequences
    assert ("f3",) in sequences and ("escape", "r") in sequences
    assert ("f4",) in sequences and ("escape", "v") in sequences
    assert ("escape", "c-m") in sequences


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
