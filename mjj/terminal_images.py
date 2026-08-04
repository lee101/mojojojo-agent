"""Terminal image presentation with no image bytes in the model transcript."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, TextIO

from .media import ImageInputError, inspect_image


PROTOCOLS = ("auto", "kitty", "ansi", "off")
ANSI_MAX_COLUMNS = 48
ANSI_MAX_ROWS = 18


@dataclass(frozen=True)
class TerminalImageResult:
    ok: bool
    protocol: str
    detail: str = ""


def terminal_image_protocol(
    *,
    environ: Mapping[str, str] | None = None,
    is_tty: bool | None = None,
) -> str:
    """Select a renderer without probing or writing to the terminal."""
    env = os.environ if environ is None else environ
    requested = env.get("MJJ_IMAGE_PROTOCOL", "auto").strip().lower()
    if requested not in PROTOCOLS:
        requested = "auto"
    if requested == "off":
        return "off"
    if requested != "auto":
        return requested
    tty = (
        bool(getattr(sys.stdout, "isatty", lambda: False)())
        if is_tty is None
        else is_tty
    )
    if not tty or env.get("TERM", "").lower() == "dumb":
        return "off"
    term = env.get("TERM", "").lower()
    if env.get("KITTY_WINDOW_ID") or "kitty" in term:
        return "kitty"
    return "ansi"


def render_terminal_image(
    path: str | Path,
    *,
    out: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
    is_tty: bool | None = None,
    columns: int = ANSI_MAX_COLUMNS,
) -> TerminalImageResult:
    """Render one image in-place; ordinary failures become compact UI state."""
    stream = out or sys.stdout
    protocol = terminal_image_protocol(environ=environ, is_tty=is_tty)
    if protocol == "off":
        return TerminalImageResult(False, "off", "terminal image display is disabled")
    try:
        info = inspect_image(path)
    except ImageInputError as exc:
        return TerminalImageResult(False, protocol, str(exc))
    if protocol == "kitty":
        rendered = _render_kitty(info.path, stream)
        if rendered.ok:
            return rendered
        fallback = _render_ansi(info.path, stream, columns)
        if fallback.ok:
            return TerminalImageResult(True, "ansi", f"Kitty failed: {rendered.detail}")
        return rendered
    return _render_ansi(info.path, stream, columns)


def _render_kitty(path: Path, out: TextIO) -> TerminalImageResult:
    kitten = shutil.which("kitten")
    command = [kitten, "icat"] if kitten else []
    if not command:
        kitty = shutil.which("kitty")
        if kitty:
            command = [kitty, "+kitten", "icat"]
    if not command:
        return TerminalImageResult(False, "kitty", "kitten icat is not installed")
    command.extend(["--align", "left", "--stdin", "no", "--loop", "0", str(path)])
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stderr": subprocess.PIPE,
        "check": False,
        "timeout": 10,
    }
    # Keep native graphics escapes in the same presentation stream when it has
    # a real descriptor. Test doubles and prompt-toolkit capture still work.
    try:
        out.fileno()
    except (AttributeError, OSError):
        return TerminalImageResult(False, "kitty", "output has no file descriptor")
    else:
        kwargs["stdout"] = out
    try:
        completed = subprocess.run(command, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return TerminalImageResult(False, "kitty", str(exc))
    if completed.returncode == 0:
        return TerminalImageResult(True, "kitty")
    error = (completed.stderr or b"").decode("utf-8", "replace").strip()
    return TerminalImageResult(
        False,
        "kitty",
        error[:160] or f"exit {completed.returncode}",
    )


def _render_ansi(path: Path, out: TextIO, columns: int) -> TerminalImageResult:
    columns = max(8, min(int(columns), ANSI_MAX_COLUMNS))
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return TerminalImageResult(
            False,
            "ansi",
            "ANSI previews require mojojojo-agent[vision]; Kitty icat works without it",
        )
    try:
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            # One half-block cell represents two vertical pixels. Typical cells
            # are roughly twice as tall as wide, so this preserves the aspect.
            rows = max(
                1,
                min(
                    ANSI_MAX_ROWS,
                    round(columns * image.height / image.width / 2),
                ),
            )
            width = max(
                1,
                min(columns, round(rows * 2 * image.width / image.height)),
            )
            image = image.resize((width, rows * 2), Image.Resampling.LANCZOS)
            pixels = image.load()
            for y in range(rows):
                line = []
                for x in range(width):
                    top = pixels[x, y * 2]
                    bottom = pixels[x, y * 2 + 1]
                    line.append(
                        f"\x1b[38;2;{top[0]};{top[1]};{top[2]}m"
                        f"\x1b[48;2;{bottom[0]};{bottom[1]};{bottom[2]}m▀"
                    )
                out.write("".join(line) + "\x1b[0m\n")
            out.flush()
    except (OSError, ValueError) as exc:
        return TerminalImageResult(False, "ansi", str(exc))
    return TerminalImageResult(True, "ansi")
