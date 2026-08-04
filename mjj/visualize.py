"""Generate deterministic standalone WebGL visualizers without model-written boilerplate."""

from __future__ import annotations

import html
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .ledger import estimate_tokens
from .media import ImageInputError, prepare_image


KINDS = ("aurora", "contours", "cells", "tunnel", "image-rift", "image-relief")
PALETTES = ("ultraviolet", "pelagic", "ember", "acid")
_TEMPLATE = Path(__file__).with_name("assets") / "visualizer.html"


class VisualizerError(ValueError):
    pass


@dataclass(frozen=True)
class VisualizerResult:
    path: Path
    relative_path: str
    kind: str
    palette: str
    seed: int
    html_bytes: int
    source_tokens: int
    milliseconds: float
    image_original_bytes: int = 0
    image_webp_bytes: int = 0

    def summary(self) -> str:
        image = f" · image {self.image_webp_bytes} B WebP" if self.image_webp_bytes else ""
        return f"created {self.relative_path} · {self.kind}/{self.palette} · {self.html_bytes} B{image}"

    def public(self) -> dict:
        result = asdict(self)
        result["path"] = str(self.path)
        return result


def render_visualizer(
    *,
    kind: str = "aurora",
    palette: str = "ultraviolet",
    seed: int = 17,
    title: str = "Living signal",
    source: str = "",
) -> str:
    _validate(kind, palette, seed, title)
    try:
        template = _TEMPLATE.read_text(encoding="utf-8")
    except OSError as exc:
        raise VisualizerError(f"visualizer template unavailable: {exc}") from exc
    config = json.dumps(
        {"kind": kind, "palette": palette, "seed": seed, "source": source},
        ensure_ascii=True,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    escaped_title = html.escape(title, quote=True)
    display_title = "<br />".join(
        f"<em>{part}</em>" if index == 1 else part
        for index, part in enumerate(escaped_title.split(" ", 2))
    )
    return (
        template.replace("__MJJ_CONFIG__", config)
        .replace("__MJJ_TITLE_HTML__", display_title)
        .replace("__MJJ_TITLE__", escaped_title)
    )


def generate_visualizer(
    output: str | Path,
    *,
    cwd: str | Path = ".",
    kind: str = "aurora",
    palette: str = "ultraviolet",
    seed: int = 17,
    title: str = "Living signal",
    image: str | Path | None = None,
    force: bool = False,
) -> VisualizerResult:
    """Create ``OUTPUT/index.html`` atomically inside ``cwd``."""

    _validate(kind, palette, seed, title)
    root = Path(cwd).expanduser().resolve()
    candidate = Path(output).expanduser()
    target = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise VisualizerError(f"output must stay inside the working directory: {output}") from None
    destination = target / "index.html"
    if destination.exists() and not force:
        raise VisualizerError(f"visualizer already exists: {destination}; use --force to replace it")
    if target.exists() and not target.is_dir():
        raise VisualizerError(f"output is not a directory: {target}")

    source = ""
    original_bytes = 0
    webp_bytes = 0
    if image is not None:
        image_path = Path(image).expanduser()
        if not image_path.is_absolute():
            image_path = root / image_path
        try:
            attachment = prepare_image(image_path)
        except ImageInputError as exc:
            raise VisualizerError(str(exc)) from exc
        source = attachment.data_url
        original_bytes = attachment.original_bytes
        webp_bytes = attachment.encoded_bytes

    started = time.perf_counter()
    rendered = render_visualizer(
        kind=kind, palette=palette, seed=seed, title=title, source=source
    )
    baseline = render_visualizer(
        kind=kind, palette=palette, seed=seed, title=title, source=""
    )
    payload = rendered.encode("utf-8")
    try:
        target.mkdir(parents=True, exist_ok=True)
        temporary = target / f".index.html.{os.getpid()}.tmp"
        temporary.write_bytes(payload)
        os.replace(temporary, destination)
    except OSError as exc:
        raise VisualizerError(f"cannot write visualizer: {exc}") from exc
    elapsed = (time.perf_counter() - started) * 1000
    return VisualizerResult(
        path=destination,
        relative_path=destination.relative_to(root).as_posix(),
        kind=kind,
        palette=palette,
        seed=seed,
        html_bytes=len(payload),
        source_tokens=estimate_tokens(baseline),
        milliseconds=round(elapsed, 3),
        image_original_bytes=original_bytes,
        image_webp_bytes=webp_bytes,
    )


def _validate(kind: str, palette: str, seed: int, title: str) -> None:
    if kind not in KINDS:
        raise VisualizerError("kind must be one of: " + ", ".join(KINDS))
    if palette not in PALETTES:
        raise VisualizerError("palette must be one of: " + ", ".join(PALETTES))
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 1_000_000:
        raise VisualizerError("seed must be an integer between 0 and 1000000")
    if not isinstance(title, str) or not title.strip() or len(title) > 120:
        raise VisualizerError("title must contain 1 to 120 characters")


__all__ = [
    "KINDS",
    "PALETTES",
    "VisualizerError",
    "VisualizerResult",
    "generate_visualizer",
    "render_visualizer",
]
