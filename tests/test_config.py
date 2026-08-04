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
permission_mode = "ask"
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
    assert config.permission_mode == "ask"
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
        ("[agent]\npermission_mode='reckless'\n", "agent.permission_mode"),
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


def test_mcp_config_resolves_paths_forwards_selected_env_and_redacts_values(tmp_path: Path):
    config_path = tmp_path / "agent.toml"
    config_path.write_text(
        """
[mcp_servers.files]
command = "python"
args = ["server.py"]
cwd = "runtime"
env = { PUBLIC = "yes" }
env_vars = ["MCP_SECRET"]
startup_timeout = 2
tool_timeout = 9
max_tools = 7
""",
        encoding="utf-8",
    )

    config = load(
        tmp_path,
        explicit=config_path,
        environ={"MJJ_HOME": str(tmp_path / "home"), "MCP_SECRET": "hidden"},
    )

    server = config.mcp_servers[0]
    assert server.command == ("python", "server.py")
    assert server.cwd == (tmp_path / "runtime").resolve()
    assert dict(server.env) == {"MCP_SECRET": "hidden", "PUBLIC": "yes"}
    assert server.max_tools == 7
    public = config.public()["mcp_servers"][0]
    assert public["env_keys"] == ["MCP_SECRET", "PUBLIC"]
    assert "hidden" not in str(public)


@pytest.mark.parametrize(
    "body, message",
    [
        ("[mcp_servers.bad]\ncommand=[]\n", "command"),
        ("[mcp_servers.bad]\ncommand='x'\nmax_tools=0\n", "max_tools"),
        ("[mcp_servers.bad]\ncommand='x'\nenv_vars='TOKEN'\n", "env_vars"),
        ("[mcp_servers.bad]\ncommand='x'\nenabled='yes'\n", "enabled"),
    ],
)
def test_invalid_mcp_config_is_actionable(tmp_path: Path, body: str, message: str):
    path = tmp_path / "bad-mcp.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load(tmp_path, explicit=path, environ={})


def test_mcp_server_count_is_bounded(tmp_path: Path):
    path = tmp_path / "too-many.toml"
    path.write_text(
        "\n".join(
            f"[mcp_servers.s{index}]\ncommand='server'" for index in range(17)
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="at most 16"):
        load(tmp_path, explicit=path, environ={})
