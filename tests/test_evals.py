from __future__ import annotations

import json
from pathlib import Path

from evals.run import (
    MAX_VERIFIER_BYTES,
    Result,
    _case_digest,
    _persist_artifacts,
    _validate_case,
    _write_bounded,
)


def _case(root: Path) -> Path:
    case = root / "case"
    (case / "repo").mkdir(parents=True)
    (case / "repo" / "value.py").write_text("value = 1\n", encoding="utf-8")
    (case / "prompt.txt").write_text("Change the value.\n", encoding="utf-8")
    (case / "check.sh").write_text("test -f value.py\n", encoding="utf-8")
    return case


def test_result_reports_total_tokens_and_failure_stage() -> None:
    result = Result("port", False, 2.0, 0.2, 100, 60, 20, 3, 8, "verifier", "bad")

    assert result.total_tokens == 120
    assert result.record()["tokens_per_pass"] is None
    assert "[verifier]" in result.line()

    result.passed = True
    assert result.record()["tokens_per_pass"] == 120


def test_case_contract_and_digest_are_content_addressed(tmp_path: Path) -> None:
    case = _case(tmp_path)
    before = _case_digest(case)

    assert _validate_case(case) == ""
    (case / "repo" / "value.py").write_text("value = 2\n", encoding="utf-8")
    assert _case_digest(case) != before
    (case / "prompt.txt").write_text("", encoding="utf-8")
    assert _validate_case(case) == "prompt.txt is empty"


def test_artifacts_are_layered_and_verifier_output_is_bounded(tmp_path: Path) -> None:
    case = _case(tmp_path)
    artifact = tmp_path / "artifacts" / "case"
    artifact.mkdir(parents=True)
    result = Result("case", False, 1.0, 0.1, 10, 0, 2, 1, 4, "verifier", "no")

    _persist_artifacts(
        artifact,
        case,
        result,
        "x" * (MAX_VERIFIER_BYTES + 20),
        "failure\n",
        case / "repo",
        keep_workspace=True,
        config={"model": "fake", "effort": "low", "variant": "skills"},
    )

    manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["result"]["total_tokens"] == 12
    assert manifest["case_sha256"] == _case_digest(case)
    assert manifest["eval_config"]["variant"] == "skills"
    assert (artifact / "workspace" / "value.py").is_file()
    output = (artifact / "verifier.stdout").read_bytes()
    assert b"truncated 20 verifier bytes" in output
    assert len(output) < MAX_VERIFIER_BYTES + 100


def test_write_bounded_keeps_small_output_exact(tmp_path: Path) -> None:
    path = tmp_path / "output"
    _write_bounded(path, "small\n")
    assert path.read_text(encoding="utf-8") == "small\n"
