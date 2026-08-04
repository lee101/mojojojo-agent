from __future__ import annotations

import io
import subprocess

from PIL import Image

from mjj import terminal_images


def test_protocol_detection_is_tty_safe_and_overrideable() -> None:
    assert terminal_images.terminal_image_protocol(environ={}, is_tty=False) == "off"
    assert (
        terminal_images.terminal_image_protocol(
            environ={"TERM": "xterm-kitty", "KITTY_WINDOW_ID": "1"},
            is_tty=True,
        )
        == "kitty"
    )
    assert (
        terminal_images.terminal_image_protocol(
            environ={"TERM": "xterm-256color"}, is_tty=True
        )
        == "ansi"
    )
    assert (
        terminal_images.terminal_image_protocol(
            environ={"MJJ_IMAGE_PROTOCOL": "kitty"}, is_tty=False
        )
        == "kitty"
    )


def test_kitty_renderer_uses_icat_without_reading_stdin(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "pixel.png"
    Image.new("RGB", (8, 4), "blue").save(path)
    calls = []
    monkeypatch.setattr(
        terminal_images.shutil,
        "which",
        lambda name: "/bin/kitten" if name == "kitten" else None,
    )

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stderr=b"")

    monkeypatch.setattr(terminal_images.subprocess, "run", run)

    class TerminalStream(io.StringIO):
        def fileno(self):
            return 1

    stream = TerminalStream()
    result = terminal_images.render_terminal_image(
        path,
        out=stream,
        environ={"MJJ_IMAGE_PROTOCOL": "kitty"},
        is_tty=False,
    )

    assert result.ok and result.protocol == "kitty"
    command, kwargs = calls[0]
    assert command[:2] == ["/bin/kitten", "icat"]
    assert command[command.index("--stdin") + 1] == "no"
    assert command[-1] == str(path)
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["timeout"] == 10


def test_failed_kitty_command_degrades_to_ansi(tmp_path, monkeypatch) -> None:
    path = tmp_path / "pixel.png"
    Image.new("RGB", (8, 4), "blue").save(path)
    monkeypatch.setattr(terminal_images.shutil, "which", lambda _name: "/bin/kitten")
    monkeypatch.setattr(
        terminal_images.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, stderr=b"unsupported terminal"
        ),
    )

    class TerminalStream(io.StringIO):
        def fileno(self):
            return 1

    stream = TerminalStream()
    result = terminal_images.render_terminal_image(
        path,
        out=stream,
        environ={"MJJ_IMAGE_PROTOCOL": "kitty"},
        is_tty=False,
    )

    assert result.ok and result.protocol == "ansi"
    assert "Kitty failed" in result.detail
    assert "▀" in stream.getvalue()


def test_ansi_fallback_is_small_and_contains_no_file_bytes(tmp_path) -> None:
    path = tmp_path / "gradient.png"
    image = Image.new("RGB", (80, 40))
    image.putdata([(x * 3, y * 6, 120) for y in range(40) for x in range(80)])
    image.save(path)
    out = io.StringIO()

    result = terminal_images.render_terminal_image(
        path,
        out=out,
        environ={"MJJ_IMAGE_PROTOCOL": "ansi"},
        is_tty=False,
    )

    rendered = out.getvalue()
    assert result.ok and result.protocol == "ansi"
    assert "▀" in rendered and "\x1b[38;2;" in rendered
    assert len(rendered) < 64 * 1024
    assert path.read_bytes() not in rendered.encode("utf-8")


def test_auto_mode_writes_nothing_when_output_is_redirected(tmp_path) -> None:
    path = tmp_path / "pixel.png"
    Image.new("RGB", (2, 2), "green").save(path)
    out = io.StringIO()

    result = terminal_images.render_terminal_image(
        path, out=out, environ={}, is_tty=False
    )

    assert not result.ok and result.protocol == "off"
    assert out.getvalue() == ""
