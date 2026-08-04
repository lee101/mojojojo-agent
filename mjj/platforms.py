"""Small operating-system seams shared by tools and process adapters."""

from __future__ import annotations

import ntpath
import os
import shlex
import subprocess
from collections.abc import Sequence


_WINDOWS_EXECUTABLE_SUFFIXES = (".exe", ".cmd", ".bat", ".com")


def is_windows(value: bool | None = None) -> bool:
    return os.name == "nt" if value is None else value


def split_command(command: str, *, windows: bool | None = None) -> list[str]:
    """Split one direct-execution command using the host command-line rules."""
    if not is_windows(windows):
        return shlex.split(command)
    return _split_windows(command)


def display_command(argv: Sequence[str], *, windows: bool | None = None) -> str:
    """Render argv for approval/UI without changing what will be executed."""
    values = list(argv)
    return subprocess.list2cmdline(values) if is_windows(windows) else shlex.join(values)


def command_name(value: str) -> str:
    """Normalize POSIX or Windows executable paths for policy matching."""
    name = ntpath.basename(value.replace("/", "\\")).casefold()
    for suffix in _WINDOWS_EXECUTABLE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _split_windows(command: str) -> list[str]:
    """Pure-Python CommandLineToArgvW-compatible splitting for direct argv."""
    arguments: list[str] = []
    index = 0
    length = len(command)
    while index < length:
        while index < length and command[index] in " \t":
            index += 1
        if index >= length:
            break
        current: list[str] = []
        quoted = False
        started = False
        while index < length:
            if command[index] in " \t" and not quoted:
                break
            backslashes = 0
            while index < length and command[index] == "\\":
                backslashes += 1
                index += 1
            if backslashes:
                started = True
            if index < length and command[index] == '"':
                current.extend("\\" * (backslashes // 2))
                if backslashes % 2:
                    current.append('"')
                else:
                    quoted = not quoted
                started = True
                index += 1
                continue
            current.extend("\\" * backslashes)
            if index >= length or (command[index] in " \t" and not quoted):
                break
            current.append(command[index])
            started = True
            index += 1
        if quoted:
            raise ValueError("No closing quotation")
        if started:
            arguments.append("".join(current))
        while index < length and command[index] in " \t":
            index += 1
    return arguments


__all__ = ["command_name", "display_command", "is_windows", "split_command"]
