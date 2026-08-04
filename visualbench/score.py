"""Cheap screenshot health metrics; not a claim to measure artistic quality."""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat


def score(path: Path) -> dict:
    with Image.open(path) as opened:
        image = opened.convert("RGB")
        image.thumbnail((640, 640))
    gray = image.convert("L")
    histogram = gray.histogram()
    count = sum(histogram)
    entropy = -sum(
        (value / count) * math.log2(value / count)
        for value in histogram
        if value
    )
    edges = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0]
    # ``getdata`` keeps the benchmark compatible with our Pillow 10.4 floor.
    pixels = list(image.getdata())
    colorfulness = statistics.fmean(max(pixel) - min(pixel) for pixel in pixels)
    clipped = sum(max(pixel) > 250 or min(pixel) < 5 for pixel in pixels) / len(pixels)
    return {
        "entropy": round(entropy, 3),
        "edge_energy": round(edges, 3),
        "colorfulness": round(colorfulness, 3),
        "clipped_fraction": round(clipped, 4),
    }


def main() -> int:
    root = Path(__file__).resolve().parent / "output"
    results = []
    for path in sorted(root.glob("*.png")):
        metrics = score(path)
        results.append({"id": path.stem, **metrics})
        print(
            f"{path.stem:22} entropy {metrics['entropy']:5.2f} · "
            f"edges {metrics['edge_energy']:5.1f} · "
            f"color {metrics['colorfulness']:5.1f} · "
            f"clipped {metrics['clipped_fraction']:.1%}"
        )
    (root / "scores.json").write_text(json.dumps(results, indent=2) + "\n")
    if not results:
        print("no screenshots; run npm run capture first")
        return 2
    return 1 if any(item["entropy"] < 3 or item["edge_energy"] < 2 for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
