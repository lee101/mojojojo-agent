"""Bounded image handling for model vision and terminal presentation.

Large screenshots are expensive twice: on the wire and in vision tokens. Every
attachment is therefore orientation-corrected, resized to a sane working edge
and encoded as WebP at quality 85 before it enters the transcript.
"""

from __future__ import annotations

import base64
import io
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps


MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 64 * 1024 * 1024
MAX_EDGE = 2048
WEBP_QUALITY = 85


class ImageInputError(ValueError):
    pass


@dataclass(frozen=True)
class ImageInfo:
    path: Path
    bytes: int
    width: int
    height: int
    format: str
    frames: int = 1

    def summary(self, *, name: str | None = None) -> str:
        label = name or self.path.name
        animation = f" · {self.frames} frames" if self.frames > 1 else ""
        return (
            f"{label} · {self.width}×{self.height} · {self.format} · "
            f"{self.bytes / 1024:.0f} KiB{animation}"
        )


@dataclass(frozen=True)
class ImageAttachment:
    path: Path
    data_url: str
    original_bytes: int
    encoded_bytes: int
    width: int
    height: int

    def response_part(self) -> dict:
        return {
            "type": "input_image",
            "image_url": self.data_url,
            "detail": "auto",
        }

    def summary(self) -> str:
        return (
            f"{self.path.name} {self.width}×{self.height} · "
            f"WebP {self.encoded_bytes / 1024:.0f} KiB"
        )


def inspect_image(path: str | Path) -> ImageInfo:
    """Validate an image and return bounded metadata without encoding its pixels."""
    source = Path(path).expanduser().resolve()
    try:
        stat = source.stat()
    except OSError as exc:
        raise ImageInputError(f"cannot read image {source}: {exc}") from exc
    if not source.is_file():
        raise ImageInputError(f"not a regular file: {source}")
    if stat.st_size > MAX_INPUT_BYTES:
        raise ImageInputError(
            f"image is {stat.st_size / 1024 / 1024:.1f} MiB; limit is "
            f"{MAX_INPUT_BYTES / 1024 / 1024:.0f} MiB"
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as opened:
                width, height = opened.size
                orientation = opened.getexif().get(274, 1)
                if orientation in (5, 6, 7, 8):
                    width, height = height, width
                if width * height > MAX_IMAGE_PIXELS:
                    raise ImageInputError(
                        f"image has {width * height:,} pixels; limit is "
                        f"{MAX_IMAGE_PIXELS:,}"
                    )
                image_format = str(opened.format or "image").upper()
                frames = max(1, int(getattr(opened, "n_frames", 1)))
            # Some plugins load/close their file while reading EXIF or frame
            # metadata. Verification must therefore use a fresh decoder.
            with Image.open(source) as verifier:
                verifier.verify()
    except ImageInputError:
        raise
    except (
        OSError,
        RuntimeError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ImageInputError(f"unsupported or corrupt image {source}: {exc}") from exc
    return ImageInfo(
        path=source,
        bytes=stat.st_size,
        width=width,
        height=height,
        format=image_format,
        frames=frames,
    )


def prepare_image(path: str | Path) -> ImageAttachment:
    info = inspect_image(path)
    source = info.path
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=WEBP_QUALITY, method=6)
            width, height = image.size
    except (OSError, ValueError) as exc:
        raise ImageInputError(f"unsupported or corrupt image {source}: {exc}") from exc
    encoded = output.getvalue()
    payload = base64.b64encode(encoded).decode("ascii")
    return ImageAttachment(
        path=source,
        data_url=f"data:image/webp;base64,{payload}",
        original_bytes=info.bytes,
        encoded_bytes=len(encoded),
        width=width,
        height=height,
    )
