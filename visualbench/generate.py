"""Regenerate the committed Signal Forge fixture through the public scaffolder."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from mjj.visualize import generate_visualizer  # noqa: E402


def main() -> int:
    procedural = generate_visualizer(
        "gallery/signal-forge",
        cwd=ROOT,
        kind="aurora",
        palette="ultraviolet",
        seed=29,
        title="Living signal field",
        force=True,
    )
    image = generate_visualizer(
        "gallery/signal-rift",
        cwd=ROOT,
        kind="image-rift",
        palette="acid",
        seed=31,
        title="Refract the familiar",
        image="gallery/image-rift/assets/mascot.webp",
        force=True,
    )
    print(procedural.summary())
    print(image.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
