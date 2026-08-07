from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

from mjj.agent_plugins import (
    MCP_SCHEMA,
    PLUGIN_SCHEMA,
    discover,
    merge_mcp_servers,
    resolve_workspace,
)
from mjj.config import MCPServerConfig
from mjj.project_docs import load
from mjj.skills import discover as discover_skills
from mjj.tools.base import ToolContext
from mjj.ledger import Ledger
from mjj.tools.skills import SkillTool


def _write_plugin(
    root: Path,
    name: str,
    *,
    skill: str | None = "greet",
    mcp: dict | None = None,
) -> Path:
    plugin = root / name
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": PLUGIN_SCHEMA,
                "name": name,
                "version": "1.0.0",
                "description": f"{name} plugin",
            }
        ),
        encoding="utf-8",
    )
    if skill is not None:
        skill_dir = plugin / "skills" / skill
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: Say hello from {name}\n---\n\nWave.\n",
            encoding="utf-8",
        )
    if mcp is not None:
        (plugin / "mcp.json").write_text(
            json.dumps({"$schema": MCP_SCHEMA, "mcpServers": mcp}),
            encoding="utf-8",
        )
    return plugin


def test_discovers_agent_plugin_skills(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".git").mkdir()
    _write_plugin(tmp_path / ".agents" / "plugins", "hello-plugin")

    skills = discover_skills(tmp_path, include_user=False)
    names = [skill.qualified_name for skill in skills]

    assert "plugin/hello-plugin:greet" in names
    result = SkillTool(include_user=False).run(
        {"name": "greet"}, ToolContext(tmp_path, Ledger())
    )
    assert result.ok
    assert "Wave." in result.output


def test_discovers_appnz_flat_markdown_skills(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("MJJ_HOME", str(home / ".mjj"))
    skill = home / ".appnz" / "skills" / "docker.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: docker\ndescription: Container workflow\n---\n\nUse docker compose.\n",
        encoding="utf-8",
    )

    skills = discover_skills(tmp_path, include_user=True)
    assert any(skill.qualified_name == "user:docker" for skill in skills)


def test_discovers_codex_and_opencode_skill_roots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".git").mkdir()
    for relative, name in (
        (".codex/skills/codex-flow", "codex-flow"),
        (".opencode/skills/open-flow", "open-flow"),
    ):
        path = tmp_path / relative
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name}\n---\n\nBody.\n",
            encoding="utf-8",
        )

    names = {skill.name for skill in discover_skills(tmp_path, include_user=False)}
    assert {"codex-flow", "open-flow"} <= names


def test_loads_stdio_mcp_from_agent_plugin(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".git").mkdir()
    _write_plugin(
        tmp_path / ".agents" / "plugins",
        "tools-plugin",
        skill=None,
        mcp={
            "local": {
                "type": "stdio",
                "command": Path(sys.executable).name,
                "args": ["${PLUGIN_ROOT}/echo-server.py"],
                "cwd": "${PLUGIN_ROOT}",
                "env": {"MARKER": "${PLUGIN_DATA}/mark"},
            }
        },
    )
    plugin_root = tmp_path / ".agents" / "plugins" / "tools-plugin"
    target = plugin_root / "echo-server.py"
    target.write_text("print('hi')\n", encoding="utf-8")
    if os.name != "nt":
        target.chmod(target.stat().st_mode | stat.S_IEXEC)

    bundle = discover(tmp_path, include_user=False)
    assert len(bundle.mcp_servers) == 1
    server = bundle.mcp_servers[0]
    assert server.name.startswith("tools-plugin__")
    assert server.cwd == plugin_root.resolve()
    env = dict(server.env)
    assert env["PLUGIN_ROOT"] == str(plugin_root.resolve())
    assert Path(env["MARKER"]) == Path(env["PLUGIN_DATA"]) / "mark"


def test_skips_remote_mcp_with_warning(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    (tmp_path / ".git").mkdir()
    _write_plugin(
        tmp_path / ".mjj" / "plugins",
        "remote-plugin",
        skill=None,
        mcp={
            "api": {
                "type": "streamable-http",
                "url": "https://example.com/mcp",
            }
        },
    )
    bundle = discover(tmp_path, include_user=False)
    assert bundle.mcp_servers == ()
    assert any("streamable-http" in warning for warning in bundle.warnings)


def test_resolve_workspace_prefers_configured_mcp_names(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    (tmp_path / ".git").mkdir()
    _write_plugin(
        tmp_path / ".agents" / "plugins",
        "tools-plugin",
        skill=None,
        mcp={
            "local": {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-c", "pass"],
                "cwd": "${PLUGIN_ROOT}",
            }
        },
    )
    configured = (
        MCPServerConfig(name="tools-plugin__local", command=(sys.executable, "-c", "print(1)")),
    )
    resolved = resolve_workspace(
        tmp_path,
        mcp_servers=configured,
        include_user=False,
    )
    assert [server.name for server in resolved.mcp_servers] == ["tools-plugin__local"]
    assert resolved.mcp_servers[0].command[-1] == "print(1)"


def test_merge_mcp_servers_caps_and_dedupes() -> None:
    left = MCPServerConfig(name="a", command=("true",))
    right = MCPServerConfig(name="a", command=("false",))
    extra = MCPServerConfig(name="b", command=("true",))
    assert merge_mcp_servers([left], [right, extra]) == (left, extra)


def test_opencode_agents_file_is_auto_loaded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("root agents", encoding="utf-8")
    opencode = tmp_path / ".opencode" / "AGENTS.md"
    opencode.parent.mkdir()
    opencode.write_text("opencode rules", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("ignored because AGENTS wins", encoding="utf-8")

    docs = load(tmp_path, include_user=False)
    assert docs.text == "root agents\n\nopencode rules"
    assert docs.sources[0].name == "AGENTS.md"
    assert docs.sources[1] == opencode.resolve()
