from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10 development environment.
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]


def _minimal_python(*arguments: str, input_text: str = "", env=None):
    return subprocess.run(
        [sys.executable, "-S", "-m", "mjj", *arguments],
        cwd=ROOT,
        env=env,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )


def test_version_starts_without_site_packages() -> None:
    completed = _minimal_python("--version")

    assert completed.returncode == 0
    assert completed.stdout.startswith("mjj ")


def test_stdlib_interactive_composer_starts_without_site_packages(tmp_path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "MJJ_HOME": str(tmp_path / "home"),
            "MJJ_TUI": "basic",
            "PYTHONUTF8": "1",
        }
    )

    completed = _minimal_python(input_text="/exit\n", env=env)

    assert completed.returncode == 0, completed.stderr
    assert "stdlib composer" in completed.stdout
    assert "coding agent" in completed.stdout


def test_base_wheel_declares_no_third_party_dependency_on_modern_python() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["dependencies"] == [
        "tomli>=2; python_version < '3.11'"
    ]
    assert set(project["project"]["optional-dependencies"]["full"]) == {
        "pillow>=10.4",
        "prompt-toolkit>=3.0.52",
    }
