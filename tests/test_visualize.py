from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from mjj.cli import main
from mjj.media import prepare_image
from mjj.visualize import KINDS, VisualizerError, generate_visualizer, render_visualizer
from visualbench.budget_bench import benchmark
from visualbench.score import score


def test_generates_standalone_deterministic_visualizer(tmp_path: Path) -> None:
    result = generate_visualizer(
        "signal",
        cwd=tmp_path,
        kind="cells",
        palette="ember",
        seed=42,
        title="Cellular bloom",
    )
    source = result.path.read_text(encoding="utf-8")

    assert result.path == tmp_path / "signal" / "index.html"
    assert result.relative_path == "signal/index.html"
    assert result.html_bytes == len(source.encode("utf-8"))
    assert result.source_tokens > 2_000
    assert '"kind":"cells"' in source
    assert '"palette":"ember"' in source
    assert "window.__VISUALBENCH_READY__" in source
    assert "https://" not in source
    assert all(kind in source for kind in KINDS)

    with pytest.raises(VisualizerError, match="already exists"):
        generate_visualizer("signal", cwd=tmp_path)


def test_committed_visualbench_fixture_matches_generator() -> None:
    root = Path(__file__).resolve().parents[1]
    fixture = root / "visualbench" / "gallery" / "signal-forge" / "index.html"
    expected = render_visualizer(
        kind="aurora",
        palette="ultraviolet",
        seed=29,
        title="Living signal field",
    )

    assert fixture.read_text(encoding="utf-8") == expected
    image_fixture = root / "visualbench" / "gallery" / "signal-rift" / "index.html"
    image = root / "visualbench" / "gallery" / "image-rift" / "assets" / "mascot.webp"
    transformed = render_visualizer(
        kind="image-rift",
        palette="acid",
        seed=31,
        title="Refract the familiar",
        source=prepare_image(image).data_url,
    )
    assert image_fixture.read_text(encoding="utf-8") == transformed


def test_readme_showcase_is_real_webp_with_strong_80_20_hierarchy() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "docs" / "assets" / "visualbench-signal-forge.webp"

    with Image.open(path) as image:
        assert image.format == "WEBP"
        assert image.size == (1280, 800)
        sample = image.convert("RGB").resize((160, 100))
        luminance = [
            _relative_luminance(sample.getpixel((x, y)))
            for y in range(sample.height)
            for x in range(sample.width)
        ]
        dark_fraction = sum(value < 0.2 for value in luminance) / len(luminance)
        corner = _relative_luminance(sample.getpixel((1, 1)))

    assert path.stat().st_size < 100_000
    assert 0.78 <= dark_fraction <= 0.90
    assert 1.05 / (corner + 0.05) >= 7.0


def test_title_is_escaped_and_output_cannot_escape_workspace(tmp_path: Path) -> None:
    rendered = render_visualizer(title='<script>alert("x")</script>')

    assert "<script>alert" not in rendered
    assert "&lt;script&gt;" in rendered
    with pytest.raises(VisualizerError, match="inside"):
        generate_visualizer("../outside", cwd=tmp_path)


def test_source_images_are_precompressed_and_embedded_as_webp(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (2400, 1200), "teal").save(source)

    result = generate_visualizer(
        "rift",
        cwd=tmp_path,
        kind="image-rift",
        image=source,
    )
    document = result.path.read_text(encoding="utf-8")

    assert result.image_original_bytes == source.stat().st_size
    assert result.image_webp_bytes > 0
    assert "data:image/webp;base64," in document


def test_visualize_cli_creates_output_and_reports_json(tmp_path: Path, capsys) -> None:
    assert main(
        [
            "visualize",
            "field",
            "-C",
            str(tmp_path),
            "--kind",
            "tunnel",
            "--json",
        ]
    ) == 0

    assert (tmp_path / "field" / "index.html").is_file()
    assert '"kind": "tunnel"' in capsys.readouterr().out


def test_visual_interest_proxy_separates_flat_and_structured_images(tmp_path: Path) -> None:
    flat = tmp_path / "flat.png"
    Image.new("RGB", (256, 256), "#202020").save(flat)
    structured = tmp_path / "structured.png"
    image = Image.new("RGB", (256, 256), "#090018")
    draw = ImageDraw.Draw(image)
    for index in range(0, 256, 8):
        draw.ellipse(
            (index - 45, index // 2 - 20, index + 90, index // 2 + 70),
            outline=(255 - index // 2, 40 + index // 2, 220),
            width=4,
        )
    image.save(structured)

    flat_score = score(flat)
    structured_score = score(structured)

    assert flat_score["active_tiles_fraction"] == 0
    assert structured_score["active_tiles_fraction"] > 0.5
    assert structured_score["interest_score"] > flat_score["interest_score"] + 25


def test_budget_benchmark_reports_zero_schema_tax_and_real_expansion() -> None:
    report = benchmark(iterations=2)
    tokens = report["tokens"]

    assert tokens["always_on_schema_tax"] == 0
    assert tokens["generated_source"] > tokens["first_use_total"]
    assert tokens["repeat_use_amplification"] > tokens["first_use_amplification"]
    assert tokens["minimum_lossless_result_budget"] == 24


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    linear = []
    for channel in rgb:
        value = channel / 255
        linear.append(
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
