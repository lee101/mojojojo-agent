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
model = "gpt-project"
effort = "medium"
verbosity = "high"
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
        environ={"MJJ_HOME": str(tmp_path / "home"), "MJJ_EFFORT": "max"},
    )

    assert config.model == "gpt-project"
    assert config.effort == "max"
    assert config.verbosity == "high"
    assert config.tool_budget == 321
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
        ("[tools]\nbudget=0\n", "tools.budget"),
        ("[skills]\npaths='nope'\n", "skills.paths"),
    ],
)
def test_invalid_config_is_actionable(tmp_path: Path, content: str, message: str):
    path = tmp_path / "bad.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load(tmp_path, explicit=path, environ={})
