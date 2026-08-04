"""Bounded image inputs for model vision.

Large screenshots are expensive twice: on the wire and in vision tokens. Every
attachment is therefore orientation-corrected, resized to a sane working edge
and encoded as WebP at quality 85 before it enters the transcript.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps


MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_EDGE = 2048
WEBP_QUALITY = 85


class ImageInputError(ValueError):
    pass


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


def prepare_image(path: str | Path) -> ImageAttachment:
    source = Path(path).expanduser().resolve()
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise ImageInputError(f"cannot read image {source}: {exc}") from exc
    if size > MAX_INPUT_BYTES:
        raise ImageInputError(
            f"image is {size / 1024 / 1024:.1f} MiB; limit is "
            f"{MAX_INPUT_BYTES / 1024 / 1024:.0f} MiB"
        )
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
        original_bytes=size,
        encoded_bytes=len(encoded),
        width=width,
        height=height,
    )
