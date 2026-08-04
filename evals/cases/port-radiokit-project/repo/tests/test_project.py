import json
import math

from radiokit.analysis import analyze
from radiokit.cli import main
from radiokit.io import read_samples


def test_report_and_input_parser(tmp_path) -> None:
    path = tmp_path / "samples.txt"
    path.write_text("0, 1, 0, -1\n" * 32, encoding="utf-8")
    samples = read_samples(path)
    report = analyze(samples, 32.0, 8.0)
    assert report.samples == 128
    assert math.isclose(report.rms, math.sqrt(0.5))
    assert report.dominant_lag == 4


def test_cli_emits_json(tmp_path, capsys) -> None:
    path = tmp_path / "samples.txt"
    path.write_text("0 1 0 -1 " * 16, encoding="utf-8")
    assert main([str(path), "--rate", "32", "--tone", "8"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["samples"] == 64
    assert payload["target_hz"] == 8.0
