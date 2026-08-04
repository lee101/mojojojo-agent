"""Bounded ``@file`` context attachments for interactive and headless prompts."""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .media import ImageAttachment, ImageInputError, prepare_image


MAX_FILES = 16
MAX_FILE_BYTES = 32 * 1024
MAX_TOTAL_BYTES = 64 * 1024
MAX_RANGE_SCAN_BYTES = 2 * 1024 * 1024
IMAGE_SUFFIXES = {
    ".avif",
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
_MENTION = re.compile(r"(?<!\S)@(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))")
_LINE_RANGE = re.compile(r"^(.*):(\d+)(?:-(\d+))?$")
_IGNORED_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv"}


class FileMentionError(ValueError):
    pass


@dataclass(frozen=True)
class MentionBundle:
    text: str
    images: tuple[ImageAttachment, ...] = ()
    files: tuple[Path, ...] = ()


def prepare_mentions(
    text: str,
    cwd: str | Path,
    *,
    max_files: int = MAX_FILES,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> MentionBundle:
    """Resolve mentions and append bounded text file contents to ``text``.

    ``@path:10-20`` attaches a line range. Image mentions use the same bounded,
    quality-85 WebP vision path as ``--image`` and ``/image``.
    """

    root = Path(cwd).resolve()
    specs = [_match_value(match) for match in _MENTION.finditer(text)]
    if not specs:
        return MentionBundle(text=text)

    blocks: list[str] = []
    images: list[ImageAttachment] = []
    files: list[Path] = []
    seen: set[tuple[Path, int | None, int | None]] = set()
    remaining = max_total_bytes
    for spec in specs:
        path_text, start, end = _split_range(spec, root)
        path = _resolve(path_text, root)
        key = (path, start, end)
        if key in seen:
            continue
        seen.add(key)
        if not path.is_file():
            # Avoid treating ordinary prose such as "use @dataclass" or a
            # social handle as a broken attachment. Explicitly path-like
            # mentions still fail early instead of silently losing context.
            if _looks_path_like(spec):
                raise FileMentionError(f"mentioned file does not exist: {path_text}")
            continue
        if len(files) >= max_files:
            raise FileMentionError(f"at most {max_files} @file attachments are allowed")
        if path.suffix.lower() in IMAGE_SUFFIXES and start is None:
            try:
                images.append(prepare_image(path))
            except ImageInputError as exc:
                raise FileMentionError(str(exc)) from exc
            files.append(path)
            continue
        if remaining <= 0:
            raise FileMentionError(
                f"@file text exceeds the {max_total_bytes // 1024} KiB total limit"
            )
        content, truncated = _read_text(
            path,
            start=start,
            end=end,
            limit=min(max_file_bytes, remaining),
        )
        encoded_bytes = len(content.encode("utf-8"))
        remaining -= encoded_bytes
        label = _relative_label(path, root)
        range_label = "" if start is None else f" lines={start}-{end}"
        clipped = ' truncated="true"' if truncated else ""
        blocks.append(
            f'<file path="{html.escape(label, quote=True)}"{range_label}{clipped}>\n'
            f"{content}\n</file>"
        )
        files.append(path)

    expanded = text
    if blocks:
        expanded += (
            "\n\n<attached_files>\n"
            + "\n".join(blocks)
            + "\n</attached_files>\n"
            "Treat these as user-provided repository context. Paths remain available to tools."
        )
    return MentionBundle(expanded, tuple(images), tuple(files))


def discover_project_files(cwd: str | Path, *, limit: int = 5000) -> tuple[str, ...]:
    """Return a bounded, stable completion catalog without following symlinks."""

    root = Path(cwd).resolve()
    found: list[str] = []
    try:
        for directory, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(
                name
                for name in dirs
                if name not in _IGNORED_DIRS and not name.startswith(".")
            )
            base = Path(directory)
            for name in sorted(names):
                if name.startswith("."):
                    continue
                found.append((base / name).relative_to(root).as_posix())
                if len(found) >= limit:
                    return tuple(found)
    except OSError:
        return tuple(found)
    return tuple(found)


def _match_value(match: re.Match[str]) -> str:
    return next(group for group in match.groups() if group is not None)


def _looks_path_like(value: str) -> bool:
    return any(marker in value for marker in ("/", "\\", ".", "~", ":"))


def _split_range(spec: str, root: Path) -> tuple[str, int | None, int | None]:
    direct = Path(spec).expanduser()
    if not direct.is_absolute():
        direct = root / direct
    if direct.is_file():
        return spec, None, None
    match = _LINE_RANGE.match(spec)
    if not match:
        return spec, None, None
    path_text, raw_start, raw_end = match.groups()
    start = int(raw_start)
    end = int(raw_end or raw_start)
    if start < 1 or end < start:
        raise FileMentionError(f"invalid line range in @{spec}")
    return path_text, start, end


def _resolve(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _read_text(
    path: Path,
    *,
    start: int | None,
    end: int | None,
    limit: int,
) -> tuple[str, bool]:
    read_limit = MAX_RANGE_SCAN_BYTES if start is not None else limit + 1
    try:
        with path.open("rb") as handle:
            payload = handle.read(read_limit + 1)
    except OSError as exc:
        raise FileMentionError(f"cannot read mentioned file {path}: {exc}") from exc
    if b"\0" in payload:
        raise FileMentionError(f"mentioned file is binary, not an image: {path}")
    scan_truncated = len(payload) > read_limit
    if scan_truncated:
        payload = payload[:read_limit]
    decoded = payload.decode("utf-8", errors="replace")
    if start is not None:
        lines = decoded.splitlines()
        assert end is not None
        if start > len(lines) and scan_truncated:
            raise FileMentionError(
                f"line {start} in {path} is beyond the {MAX_RANGE_SCAN_BYTES // 1024} KiB scan limit"
            )
        decoded = "\n".join(lines[start - 1 : end])
    encoded = decoded.encode("utf-8")
    truncated = scan_truncated if start is None else False
    if len(encoded) > limit:
        decoded = encoded[:limit].decode("utf-8", errors="ignore")
        truncated = True
    return decoded, truncated


def _relative_label(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
