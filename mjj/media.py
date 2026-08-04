"""Bounded image handling for model vision and terminal presentation.

Large screenshots are expensive twice: on the wire and in vision tokens. The
optional pixel backend orientation-corrects, resizes, and encodes attachments
as quality-85 WebP. The dependency-free path accepts already-bounded common
web formats without decoding them.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
    encoded_format: str = "WebP"

    def response_part(self) -> dict:
        return {
            "type": "input_image",
            "image_url": self.data_url,
            "detail": "auto",
        }

    def summary(self) -> str:
        return (
            f"{self.path.name} {self.width}×{self.height} · "
            f"{self.encoded_format} {self.encoded_bytes / 1024:.0f} KiB"
        )


def _pillow() -> tuple[Any, Any] | None:
    """Load the optional pixel backend only when an operation needs it."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    return Image, ImageOps


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
    pillow = _pillow()
    if pillow is None:
        try:
            width, height, image_format, frames = _inspect_header(source)
        except (OSError, ValueError) as exc:
            raise ImageInputError(
                f"unsupported or corrupt image {source}: {exc}"
            ) from exc
        if width < 1 or height < 1:
            raise ImageInputError(f"unsupported or corrupt image {source}: invalid size")
        if width * height > MAX_IMAGE_PIXELS:
            raise ImageInputError(
                f"image has {width * height:,} pixels; limit is {MAX_IMAGE_PIXELS:,}"
            )
        return ImageInfo(
            path=source,
            bytes=stat.st_size,
            width=width,
            height=height,
            format=image_format,
            frames=frames,
        )

    Image, _ = pillow
    import warnings

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
    pillow = _pillow()
    if pillow is None:
        if max(info.width, info.height) > MAX_EDGE:
            raise ImageInputError(
                f"image edge is {max(info.width, info.height)} pixels; install "
                "mojojojo-agent[vision] to resize it to the 2048-pixel limit"
            )
        mime = {
            "GIF": "image/gif",
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
        }[info.format]
        try:
            encoded = source.read_bytes()
        except OSError as exc:
            raise ImageInputError(f"cannot read image {source}: {exc}") from exc
        payload = base64.b64encode(encoded).decode("ascii")
        return ImageAttachment(
            path=source,
            data_url=f"data:{mime};base64,{payload}",
            original_bytes=info.bytes,
            encoded_bytes=len(encoded),
            width=info.width,
            height=info.height,
            encoded_format=info.format,
        )

    Image, ImageOps = pillow
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


def _inspect_header(path: Path) -> tuple[int, int, str, int]:
    """Read dimensions from common model-supported formats without decoding pixels."""
    with path.open("rb") as stream:
        header = stream.read(32)
        if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
            if header[12:16] != b"IHDR":
                raise ValueError("PNG has no IHDR header")
            return (
                int.from_bytes(header[16:20], "big"),
                int.from_bytes(header[20:24], "big"),
                "PNG",
                1,
            )
        if header[:6] in (b"GIF87a", b"GIF89a") and len(header) >= 10:
            return (
                int.from_bytes(header[6:8], "little"),
                int.from_bytes(header[8:10], "little"),
                "GIF",
                1,
            )
        if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return (*_webp_size(header, stream), "WEBP", 1)
        if header.startswith(b"\xff\xd8"):
            stream.seek(2)
            return (*_jpeg_size(stream), "JPEG", 1)
    raise ValueError("expected PNG, JPEG, GIF, or WebP")


def _jpeg_size(stream) -> tuple[int, int]:
    while True:
        marker_start = stream.read(1)
        if not marker_start:
            break
        if marker_start != b"\xff":
            continue
        marker = stream.read(1)
        while marker == b"\xff":
            marker = stream.read(1)
        if not marker:
            break
        code = marker[0]
        if code in (0xD8, 0xD9) or 0xD0 <= code <= 0xD7:
            continue
        raw_length = stream.read(2)
        if len(raw_length) != 2:
            break
        length = int.from_bytes(raw_length, "big")
        if length < 2:
            break
        if code in {
            0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
        }:
            body = stream.read(5)
            if len(body) != 5:
                break
            return (
                int.from_bytes(body[3:5], "big"),
                int.from_bytes(body[1:3], "big"),
            )
        stream.seek(length - 2, 1)
    raise ValueError("JPEG dimensions were not found")


def _webp_size(header: bytes, stream) -> tuple[int, int]:
    kind = header[12:16]
    body = header[20:]
    if kind == b"VP8X" and len(body) >= 10:
        return (
            int.from_bytes(body[4:7], "little") + 1,
            int.from_bytes(body[7:10], "little") + 1,
        )
    if kind == b"VP8L" and len(body) >= 5 and body[0] == 0x2F:
        bits = int.from_bytes(body[1:5], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if kind == b"VP8 ":
        data = body + stream.read(16)
        marker = data.find(b"\x9d\x01\x2a")
        if marker >= 0 and len(data) >= marker + 7:
            width = int.from_bytes(data[marker + 3 : marker + 5], "little") & 0x3FFF
            height = int.from_bytes(data[marker + 5 : marker + 7], "little") & 0x3FFF
            return width, height
    raise ValueError("WebP dimensions were not found")
