from __future__ import annotations

from pathlib import Path

import pytest

from mjj.config import ConfigError, load


def test_project_config_and_environment_precedence(tmp_path: Path):
    project = tmp_path / "repo"
    child = project / "src"
    (project / ".git").mkdir(parents=True)
    (project / ".mjj").mkdir()
    child.mkdir()
    (project / ".mjj" / "config.toml").write_text(
        """
[agent]
provider = "openpaths"
model = "gpt-project"
effort = "medium"
verbosity = "high"
project_doc_max_bytes = 4096
auto_next_steps = true
auto_next_idea = false
auto_max_turns = 7
[tools]
budget = 321
disabled = ["shell"]
[skills]
paths = ["../skills"]
""",
        encoding="utf-8",
    )

    config = load(
        child,
        environ={
            "MJJ_HOME": str(tmp_path / "home"),
            "MJJ_EFFORT": "max",
            "MJJ_AUTO_NEXT_IDEA": "true",
            "MJJ_AUTO_MAX_TURNS": "3",
        },
    )

    assert config.model == "gpt-project"
    assert config.provider == "openpaths"
    assert config.effort == "max"
    assert config.verbosity == "high"
    assert config.tool_budget == 321
    assert config.project_doc_max_bytes == 4096
    assert config.auto_next_steps is True
    assert config.auto_next_idea is True
    assert config.auto_max_turns == 3
    assert config.disabled_tools == ("shell",)
    assert config.skill_paths == ((project / "skills").resolve(),)
    assert config.files == ((project / ".mjj" / "config.toml").resolve(),)


def test_explicit_config_is_last_file_layer(tmp_path: Path):
    explicit = tmp_path / "chosen.toml"
    explicit.write_text("[agent]\nmodel='gpt-chosen'\n", encoding="utf-8")
    assert load(tmp_path, explicit=explicit, environ={}).model == "gpt-chosen"


@pytest.mark.parametrize(
    "content, message",
    [
        ("[agent]\neffort='enormous'\n", "agent.effort"),
        ("[agent]\nprovider='mystery'\n", "agent.provider"),
        ("[tools]\nbudget=0\n", "tools.budget"),
        ("[skills]\npaths='nope'\n", "skills.paths"),
        ("[agent]\nproject_doc_max_bytes=-1\n", "agent.project_doc_max_bytes"),
        ("[agent]\nauto_max_turns=-1\n", "agent.auto_max_turns"),
        ("[agent]\nauto_next_steps='yes'\n", "agent.auto_next_steps"),
    ],
)
def test_invalid_config_is_actionable(tmp_path: Path, content: str, message: str):
    path = tmp_path / "bad.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load(tmp_path, explicit=path, environ={})


def test_invalid_autonomy_environment_boolean_is_actionable(tmp_path: Path):
    with pytest.raises(ConfigError, match="MJJ_AUTO_NEXT_STEPS"):
        load(tmp_path, environ={"MJJ_AUTO_NEXT_STEPS": "sometimes"})
