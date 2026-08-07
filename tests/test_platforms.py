from pathlib import Path

from mjj.platforms import command_name, display_command, split_command
from mjj.search.vectors import _library_candidates
from mjj.tools import build_registry


def test_every_core_tool_imports_on_the_current_platform() -> None:
    registry = build_registry(include_user_skills=False)

    assert set(registry.tools) == {
        "apply_patch",
        "check",
        "checkpoint",
        "delegate",
        "display_image",
        "list",
        "navigate",
        "py",
        "read",
        "read_image",
        "search",
        "shell",
        "skill",
        "update_plan",
        "verify",
    }


def test_windows_command_split_preserves_drive_paths_and_escaped_quotes() -> None:
    command = r'"C:\Program Files\Python\python.exe" -c "print(\"ok\")" ""'

    assert split_command(command, windows=True) == [
        r"C:\Program Files\Python\python.exe",
        "-c",
        'print("ok")',
        "",
    ]


def test_windows_command_split_rejects_unclosed_quotes() -> None:
    try:
        split_command('python "unterminated', windows=True)
    except ValueError as exc:
        assert "closing quotation" in str(exc)
    else:
        raise AssertionError("unterminated Windows command should fail")


def test_windows_command_split_keeps_backslash_only_arguments() -> None:
    assert split_command("python \\", windows=True) == ["python", "\\"]


def test_command_policy_name_accepts_windows_executable_paths() -> None:
    assert command_name(r"C:\Tools\RG.EXE") == "rg"
    assert command_name(r"C:\Program Files\Git\bin\git.cmd") == "git"
    assert command_name("/usr/bin/git") == "git"


def test_command_display_uses_host_specific_quoting() -> None:
    argv = [r"C:\Program Files\Python\python.exe", "-c", "print('ok')"]

    windows = display_command(argv, windows=True)
    posix = display_command(argv, windows=False)

    assert windows.startswith('"C:\\Program Files\\Python\\python.exe"')
    assert posix.startswith("'C:\\Program Files\\Python\\python.exe'")


def test_mojo_embed_discovery_includes_windows_and_linux_libraries(
    monkeypatch,
) -> None:
    monkeypatch.delenv("MJJ_MOJO_EMBED_LIB", raising=False)
    names = {Path(candidate).name for candidate in _library_candidates()}

    assert "mojo_embed.dll" in names
    assert "libmojo_embed.so" in names
