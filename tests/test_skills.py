from __future__ import annotations

from pathlib import Path

from mjj.ledger import Ledger
from mjj.skills import discover, find
from mjj.tools.base import ToolContext
from mjj.tools.skills import SkillTool


def write_skill(root: Path, name: str, description: str = "Do the useful thing") -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    path = directory / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# Workflow\n\nRun the proof.\n",
        encoding="utf-8",
    )
    return path


def test_discovers_project_skill_and_loads_bundled_files(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    path = write_skill(tmp_path / ".mjj" / "skills", "prove-it")
    (path.parent / "check.sh").write_text("exit 0\n", encoding="utf-8")

    skills = discover(tmp_path, include_user=False)
    assert [(skill.qualified_name, skill.description) for skill in skills] == [
        (
            "builtin:visualizer",
            "Build deterministic procedural WebGL or image-transform visualizers with minimal model-written source and measurable visualbench output.",
        ),
        ("project:prove-it", "Do the useful thing")
    ]
    result = SkillTool(include_user=False).run(
        {"name": "prove-it"}, ToolContext(tmp_path, Ledger())
    )
    assert result.ok
    assert "Run the proof." in result.output
    assert "check.sh" in result.output
    assert str(path) in result.output


def test_no_name_returns_compact_catalog(tmp_path: Path):
    write_skill(tmp_path / ".agents" / "skills", "one", "First workflow")
    write_skill(tmp_path / ".agents" / "skills", "two", "Second workflow")
    result = SkillTool(include_user=False).run(
        {}, ToolContext(tmp_path, Ledger())
    )
    assert result.ok
    assert result.output.splitlines() == [
        "builtin:visualizer: Build deterministic procedural WebGL or image-transform visualizers with minimal model-written source and measurable visualbench output.",
        "project:one: First workflow",
        "project:two: Second workflow",
    ]


def test_hosted_mode_does_not_discover_user_skills(tmp_path: Path, monkeypatch):
    home = tmp_path / "mjj-home"
    write_skill(home / "skills", "host-secret")
    monkeypatch.setenv("MJJ_HOME", str(home))
    assert [skill.qualified_name for skill in discover(tmp_path, include_user=False)] == [
        "builtin:visualizer"
    ]
    assert "host-secret" in [
        skill.name for skill in discover(tmp_path, include_user=True)
    ]


def test_claude_skill_compatibility_can_be_disabled(tmp_path: Path, monkeypatch):
    (tmp_path / ".git").mkdir()
    write_skill(tmp_path / ".claude" / "skills", "claude-workflow")
    assert "claude-workflow" in [
        skill.name for skill in discover(tmp_path, include_user=False)
    ]

    monkeypatch.setenv("MJJ_DISABLE_CLAUDE_CODE_SKILLS", "1")

    assert "claude-workflow" not in [
        skill.name for skill in discover(tmp_path, include_user=False)
    ]


def test_duplicate_short_names_require_qualification(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    write_skill(tmp_path / ".mjj" / "skills", "same")
    extra = write_skill(tmp_path / "extra", "same")
    skills = discover(
        tmp_path,
        include_user=False,
        extra_paths=[extra],
    )
    skill, choices = find(skills, "same")
    assert skill is None
    assert choices == ["extra:same", "project:same"]
