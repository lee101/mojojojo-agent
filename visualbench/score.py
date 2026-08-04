"""Deterministic visual health and composition proxies for captured screenshots.

The score rejects blank, flat, clipped, or spatially dead renders. It does not
claim to replace human artistic review; a generated contact sheet makes that
review cheap and the diversity matrix catches visually collapsed variants.
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat


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
    flattened = getattr(image, "get_flattened_data", None)
    pixels = list(flattened() if flattened is not None else image.getdata())
    colorfulness = statistics.fmean(max(pixel) - min(pixel) for pixel in pixels)
    clipped = sum(max(pixel) > 250 or min(pixel) < 5 for pixel in pixels) / len(pixels)
    low = _histogram_percentile(histogram, count, 0.05)
    high = _histogram_percentile(histogram, count, 0.95)
    luminance_range = high - low
    active_tiles = _active_tile_fraction(gray)
    center_border = _center_border_delta(gray)
    interest = _interest_score(
        entropy, edges, colorfulness, clipped, luminance_range, active_tiles, center_border
    )
    return {
        "entropy": round(entropy, 3),
        "edge_energy": round(edges, 3),
        "colorfulness": round(colorfulness, 3),
        "clipped_fraction": round(clipped, 4),
        "luminance_range": luminance_range,
        "active_tiles_fraction": round(active_tiles, 4),
        "center_border_delta": round(center_border, 3),
        "interest_score": round(interest, 1),
    }


def _histogram_percentile(histogram: list[int], count: int, quantile: float) -> int:
    target = count * quantile
    seen = 0
    for value, amount in enumerate(histogram):
        seen += amount
        if seen >= target:
            return value
    return 255


def _active_tile_fraction(gray: Image.Image, divisions: int = 8) -> float:
    width, height = gray.size
    active = 0
    total = 0
    for row in range(divisions):
        for column in range(divisions):
            box = (
                column * width // divisions,
                row * height // divisions,
                (column + 1) * width // divisions,
                (row + 1) * height // divisions,
            )
            tile = gray.crop(box)
            active += ImageStat.Stat(tile).stddev[0] >= 6
            total += 1
    return active / total


def _center_border_delta(gray: Image.Image) -> float:
    width, height = gray.size
    center_box = (width // 4, height // 4, width * 3 // 4, height * 3 // 4)
    center = ImageStat.Stat(gray.crop(center_box)).mean[0]
    whole = ImageStat.Stat(gray).mean[0]
    center_area = (width // 2) * (height // 2)
    border_area = width * height - center_area
    border = (whole * width * height - center * center_area) / max(border_area, 1)
    return abs(center - border)


def _interest_score(
    entropy: float,
    edges: float,
    colorfulness: float,
    clipped: float,
    luminance_range: int,
    active_tiles: float,
    center_border: float,
) -> float:
    # Balanced complexity scores above both flat fields and full-frame noise.
    entropy_balance = max(0.0, 1.0 - abs(entropy - 6.2) / 3.8)
    edge_balance = min(edges / 12.0, 1.0) * min(1.0, 48.0 / max(edges, 0.01))
    color = min(colorfulness / 38.0, 1.0)
    dynamic_range = min(luminance_range / 135.0, 1.0)
    activity = min(active_tiles / 0.75, 1.0)
    composition = min(center_border / 18.0, 1.0)
    clipping_penalty = 1.0 - max(0.0, clipped - 0.58) * 1.6
    weighted = (
        entropy_balance * 0.22
        + edge_balance * 0.18
        + color * 0.18
        + dynamic_range * 0.18
        + activity * 0.14
        + composition * 0.10
    )
    return max(0.0, min(100.0, weighted * clipping_penalty * 100.0))


def diversity(paths: list[Path]) -> dict:
    prepared: dict[str, Image.Image] = {}
    for path in paths:
        with Image.open(path) as opened:
            prepared[path.stem] = ImageOps.fit(opened.convert("RGB"), (64, 64))
    pairs = []
    names = sorted(prepared)
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            difference = ImageStat.Stat(ImageChops.difference(prepared[left], prepared[right]))
            normalized = statistics.fmean(difference.rms) / 255.0
            pairs.append({"left": left, "right": right, "distance": round(normalized, 4)})
    return {
        "minimum": min((pair["distance"] for pair in pairs), default=0.0),
        "mean": round(statistics.fmean(pair["distance"] for pair in pairs), 4) if pairs else 0.0,
        "pairs": pairs,
    }


def contact_sheet(paths: list[Path], destination: Path) -> None:
    columns = 3
    cell_width, image_height, label_height = 420, 270, 34
    rows = math.ceil(len(paths) / columns)
    sheet = Image.new("RGB", (columns * cell_width, rows * (image_height + label_height)), "#090a10")
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(paths):
        with Image.open(path) as opened:
            thumbnail = ImageOps.fit(opened.convert("RGB"), (cell_width, image_height))
        x = (index % columns) * cell_width
        y = (index // columns) * (image_height + label_height)
        sheet.paste(thumbnail, (x, y))
        draw.text((x + 12, y + image_height + 10), path.stem, fill="#e9e5dc")
    sheet.save(destination, format="WEBP", quality=85, method=6)


def main() -> int:
    root = Path(__file__).resolve().parent / "output"
    paths = sorted(root.glob("*.png"))
    capture_path = root / "results.json"
    capture = {}
    if capture_path.is_file():
        capture = {item["id"]: item for item in json.loads(capture_path.read_text())}
    results = []
    for path in paths:
        metrics = score(path)
        timing = capture.get(path.stem, {})
        results.append(
            {
                "id": path.stem,
                **metrics,
                "load_milliseconds": timing.get("loadMilliseconds"),
                "screenshot_milliseconds": timing.get("screenshotMilliseconds"),
            }
        )
        print(
            f"{path.stem:22} interest {metrics['interest_score']:5.1f} · "
            f"entropy {metrics['entropy']:4.2f} · edges {metrics['edge_energy']:5.1f} · "
            f"color {metrics['colorfulness']:5.1f} · active {metrics['active_tiles_fraction']:.0%}"
        )
    if not results:
        print("no screenshots; run npm run capture first")
        return 2
    visual_diversity = diversity(paths)
    forge_diversity = diversity([path for path in paths if path.stem.startswith("forge-")])
    contact_sheet(paths, root / "contact-sheet.webp")
    (root / "scores.json").write_text(json.dumps(results, indent=2) + "\n")
    (root / "report.json").write_text(
        json.dumps(
            {
                "scores": results,
                "diversity": visual_diversity,
                "forge_diversity": forge_diversity,
            },
            indent=2,
        )
        + "\n"
    )
    print(
        f"diversity mean {visual_diversity['mean']:.3f} · "
        f"minimum {visual_diversity['minimum']:.3f} · contact-sheet.webp"
    )
    print(
        f"Signal Forge diversity mean {forge_diversity['mean']:.3f} · "
        f"minimum {forge_diversity['minimum']:.3f}"
    )
    unhealthy = any(
        item["entropy"] < 3
        or item["edge_energy"] < 2
        or item["luminance_range"] < 30
        or item["active_tiles_fraction"] < 0.25
        for item in results
    )
    return 1 if unhealthy else 0


if __name__ == "__main__":
    raise SystemExit(main())
