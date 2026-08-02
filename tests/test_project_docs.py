from __future__ import annotations

from pathlib import Path

from mjj.agent import Agent
from mjj.project_docs import compose, load
from mjj.tools.base import Registry


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


def test_agent_loads_project_docs_once_at_startup(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    agents = tmp_path / "AGENTS.md"
    agents.write_text("first version", encoding="utf-8")
    agent = Agent(registry=Registry(), cwd=tmp_path)
    agents.write_text("later version", encoding="utf-8")

    assert agent.instructions is not None
    assert "first version" in agent.instructions
    assert "later version" not in agent.instructions
    assert agent.project_instructions.sources == (agents.resolve(),)
