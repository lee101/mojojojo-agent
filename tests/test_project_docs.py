from __future__ import annotations

from pathlib import Path

from mjj.agent import Agent
from mjj.project_docs import compose, load
from mjj.ledger import Budget, Ledger
from mjj.tools import build_registry
from mjj.tools.base import Registry, ToolContext


def test_loads_root_to_cwd_and_prefers_override(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    child = repo / "src" / "pkg"
    (repo / ".git").mkdir(parents=True)
    child.mkdir(parents=True)
    (repo / "AGENTS.md").write_text("root rules", encoding="utf-8")
    (repo / "src" / "AGENTS.md").write_text("ignored", encoding="utf-8")
    override = repo / "src" / "AGENTS.override.md"
    override.write_text("local rules", encoding="utf-8")
    leaf = child / "AGENTS.md"
    leaf.write_text("leaf rules", encoding="utf-8")

    docs = load(child)

    assert docs.text == "root rules\n\nlocal rules\n\nleaf rules"
    assert docs.sources == (
        (repo / "AGENTS.md").resolve(),
        override.resolve(),
        leaf.resolve(),
    )
    assert docs.bytes_read == len("root ruleslocal rulesleaf rules")
    assert not docs.truncated


def test_budget_is_global_and_truncation_is_exact(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_bytes(b"0123456789")

    docs = load(tmp_path, max_bytes=6)

    assert docs.text == "012345"
    assert docs.bytes_read == 6
    assert docs.truncated
    assert load(tmp_path, max_bytes=0).text == ""


def test_without_project_marker_only_cwd_is_considered(tmp_path: Path) -> None:
    child = tmp_path / "a" / "b"
    child.mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("outside", encoding="utf-8")
    (child / "AGENTS.md").write_text("inside", encoding="utf-8")

    assert load(child).text == "inside"


def test_compose_marks_project_instruction_boundary() -> None:
    docs = load(Path(__file__).parent.parent, max_bytes=32)
    combined = compose("base", docs)
    assert combined.startswith("base\n\n--- project-doc ---\n\n")


def test_agent_loads_project_docs_once_at_startup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".git").mkdir()
    agents = tmp_path / "AGENTS.md"
    agents.write_text("first version", encoding="utf-8")
    agent = Agent(registry=Registry(), cwd=tmp_path)
    agents.write_text("later version", encoding="utf-8")

    assert agent.instructions is not None
    assert "first version" in agent.instructions
    assert "later version" not in agent.instructions
    assert agent.project_instructions.sources == (agents.resolve(),)


def test_claude_and_context_are_per_directory_fallbacks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    child = repo / "src"
    (repo / ".git").mkdir(parents=True)
    child.mkdir()
    (repo / "CLAUDE.md").write_text("root claude")
    (repo / "CONTEXT.md").write_text("ignored context")
    (child / "AGENTS.md").write_text("child agents")
    (child / "CLAUDE.md").write_text("ignored child claude")

    docs = load(child)

    assert docs.text == "root claude\n\nchild agents"
    assert docs.sources == (
        (repo / "CLAUDE.md").resolve(),
        (child / "AGENTS.md").resolve(),
    )

    (repo / "CLAUDE.md").unlink()
    assert load(child).text == "ignored context\n\nchild agents"


def test_claude_compatibility_can_be_disabled_without_disabling_context(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "CLAUDE.md").write_text("claude rules")
    (tmp_path / "CONTEXT.md").write_text("legacy rules")

    docs = load(
        tmp_path,
        environ={"MJJ_DISABLE_CLAUDE_CODE_PROMPT": "true"},
    )

    assert docs.text == "legacy rules"
    assert docs.sources == ((tmp_path / "CONTEXT.md").resolve(),)


def test_user_agents_precedes_claude_fallback_and_combines_with_project(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    mjj_home = tmp_path / "mjj-home"
    (repo / ".git").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    mjj_home.mkdir()
    (repo / "AGENTS.md").write_text("project rules")
    global_agents = mjj_home / "AGENTS.md"
    global_agents.write_text("personal rules")
    global_claude = home / ".claude" / "CLAUDE.md"
    global_claude.write_text("claude personal rules")
    environ = {"MJJ_HOME": str(mjj_home), "HOME": str(home)}

    docs = load(repo, include_user=True, environ=environ)

    assert docs.text == "personal rules\n\nproject rules"
    assert docs.sources == (global_agents.resolve(), (repo / "AGENTS.md").resolve())

    global_agents.unlink()
    fallback = load(repo, include_user=True, environ=environ)
    assert fallback.text == "claude personal rules\n\nproject rules"
    assert fallback.sources[0] == global_claude.resolve()


def test_opencode_global_agents_is_reused_before_claude(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    opencode = home / ".config" / "opencode" / "AGENTS.md"
    claude = home / ".claude" / "CLAUDE.md"
    (repo / ".git").mkdir(parents=True)
    opencode.parent.mkdir(parents=True)
    claude.parent.mkdir(parents=True)
    opencode.write_text("opencode personal rules")
    claude.write_text("claude personal rules")

    docs = load(
        repo,
        include_user=True,
        environ={"MJJ_HOME": str(home / ".mjj"), "HOME": str(home)},
    )

    assert docs.text == "opencode personal rules"
    assert docs.sources == (opencode.resolve(),)


def test_project_rules_get_budget_priority_over_user_rules(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    mjj_home = tmp_path / "home"
    (repo / ".git").mkdir(parents=True)
    mjj_home.mkdir()
    (repo / "AGENTS.md").write_text("project")
    (mjj_home / "AGENTS.md").write_text("personal")

    docs = load(
        repo,
        max_bytes=10,
        include_user=True,
        environ={"MJJ_HOME": str(mjj_home)},
    )

    assert docs.text == "per\n\nproject"
    assert docs.bytes_read == 10
    assert docs.truncated


def test_injected_environment_home_controls_default_mjj_home(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    (repo / ".git").mkdir(parents=True)
    (home / ".mjj").mkdir(parents=True)
    (home / ".mjj" / "AGENTS.md").write_text("personal")

    docs = load(repo, include_user=True, environ={"HOME": str(home)})

    assert docs.text == "personal"
    assert docs.sources == ((home / ".mjj" / "AGENTS.md").resolve(),)


def test_agent_can_exclude_service_account_user_rules(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    (repo / ".git").mkdir(parents=True)
    home.mkdir()
    (repo / "CLAUDE.md").write_text("tenant project rules")
    (home / "AGENTS.md").write_text("service account secret rules")
    monkeypatch.setenv("MJJ_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))

    agent = Agent(
        registry=Registry(),
        cwd=repo,
        include_user_instructions=False,
    )

    assert "tenant project rules" in agent.instructions
    assert "service account secret rules" not in agent.instructions
    assert agent.project_instructions.sources == ((repo / "CLAUDE.md").resolve(),)


def test_agent_project_doc_budget_is_shared_with_lazy_discovery(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    nested = repo / "src"
    (repo / ".git").mkdir(parents=True)
    nested.mkdir()
    (repo / "AGENTS.md").write_text("root")
    (nested / "AGENTS.md").write_text("nested rules")
    target = nested / "module.py"
    target.write_text("value = 1\n")
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    agent = Agent(
        registry=build_registry(only=["fs"]),
        cwd=repo,
        project_doc_max_bytes=10,
    )

    result = agent.registry.dispatch(
        "read", '{"path":"src/module.py"}', agent.ctx
    )

    assert "nested" in result.output
    assert "nested rules" not in result.output
    assert agent.project_instructions.bytes_read == 4
    assert agent.ctx.state["scoped-project-docs"].remaining == 0


def test_nested_project_docs_are_injected_once_on_first_tool_access(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("root rules")
    nested = tmp_path / "src" / "feature"
    nested.mkdir(parents=True)
    override = nested / "AGENTS.override.md"
    override.write_text("run the feature-specific check")
    target = nested / "module.py"
    target.write_text("value = 1\n")
    context = ToolContext(tmp_path, Ledger(Budget(default=300)))
    registry = build_registry(only=["fs"])

    first = registry.dispatch("read", '{"path":"src/feature/module.py"}', context)
    second = registry.dispatch("read", '{"path":"src/feature/module.py"}', context)

    assert "scoped project instructions" in first.output
    assert "run the feature-specific check" in first.output
    assert first.meta["project_docs"] == [str(override.resolve())]
    assert "scoped project instructions" not in second.output
    assert context.ledger.tool_calls == 2


def test_nested_claude_file_is_injected_lazily(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "packages" / "feature"
    nested.mkdir(parents=True)
    claude = nested / "CLAUDE.md"
    claude.write_text("feature claude rules")
    target = nested / "module.py"
    target.write_text("value = 1\n")
    context = ToolContext(tmp_path, Ledger(Budget(default=300)))

    result = build_registry(only=["fs"]).dispatch(
        "read", '{"path":"packages/feature/module.py"}', context
    )

    assert "feature claude rules" in result.output
    assert result.meta["project_docs"] == [str(claude.resolve())]


def test_nested_project_docs_outside_workspace_are_not_injected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "AGENTS.md").write_text("outside rules")
    target = outside / "file.txt"
    target.write_text("content")
    context = ToolContext(workspace, Ledger())

    result = build_registry(only=["fs"]).dispatch(
        "read", f'{{"path":"{target}"}}', context
    )

    assert result.ok
    assert "outside rules" not in result.output
